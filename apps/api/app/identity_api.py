"""Multi-user administration, company-scoped groups, photo, TOTP and account mail."""
from __future__ import annotations

from datetime import timedelta, timezone
from hashlib import sha256
from io import BytesIO
import secrets
from urllib.parse import quote

import httpx
from fastapi import (APIRouter, Depends, File, HTTPException, Query, Response,
                     UploadFile)
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel, Field, field_validator
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import PERMISSION_KEYS, accessible_companies, permissions, require_permission
from app.config import get_settings
from app.db import get_session
from app.mail import MailUnavailable, mail_status, send_mail
from app.mfa import (decrypt_seed, encrypt_seed, enrollment_uri, new_seed,
                     recover, recovery_codes, valid_counter)
from app.models import (AccessGroup, AccountAction, AuditEvent, Company, GroupCompanyGrant,
                        GroupMembership, User, UserCompanyGrant, now)
from app.schemas import valid_cnpj
from app.security import admin_only, current_user, hash_password, verify_password

router = APIRouter(prefix="/api/v1", tags=["identity"])
ROLES = frozenset({"admin", "manager", "operator", "viewer"})
MAX_PHOTO = 1024 * 1024


class UserInput(BaseModel):
    username: str = Field(min_length=5, max_length=254)
    full_name: str = Field(min_length=2, max_length=160)
    position: str = Field(default="", max_length=120)
    role: str = "viewer"
    active: bool = True
    company_access_mode: str = "restricted"
    company_ids: list[str] = Field(default_factory=list, max_length=500)
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    permission_keys: list[str] = Field(default_factory=list, max_length=30)
    invite: bool = True

    @field_validator("username")
    @classmethod
    def email(cls, value: str) -> str:
        from re import fullmatch
        cleaned = value.strip().lower()
        if not fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", cleaned):
            raise ValueError("Informe um e-mail válido")
        return cleaned

    @field_validator("role")
    @classmethod
    def role_valid(cls, value: str) -> str:
        if value not in ROLES:
            raise ValueError("Perfil inválido")
        return value

    @field_validator("company_access_mode")
    @classmethod
    def access_valid(cls, value: str) -> str:
        if value not in {"all", "restricted"}:
            raise ValueError("Acesso a empresas inválido")
        return value

    @field_validator("permission_keys")
    @classmethod
    def keys_valid(cls, value: list[str]) -> list[str]:
        if set(value) - PERMISSION_KEYS:
            raise ValueError("Permissões desconhecidas")
        return list(dict.fromkeys(value))


class UserPatch(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=160)
    position: str | None = Field(None, max_length=120)
    role: str | None = None
    active: bool | None = None
    company_access_mode: str | None = None
    company_ids: list[str] | None = None
    group_ids: list[str] | None = None
    permission_keys: list[str] | None = None

    @field_validator("role")
    @classmethod
    def role_valid(cls, value: str | None) -> str | None:
        if value is not None and value not in ROLES:
            raise ValueError("Perfil inválido")
        return value

    @field_validator("company_access_mode")
    @classmethod
    def access_valid(cls, value: str | None) -> str | None:
        if value is not None and value not in {"all", "restricted"}:
            raise ValueError("Acesso a empresas inválido")
        return value

    @field_validator("permission_keys")
    @classmethod
    def permissions_valid(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and set(value) - PERMISSION_KEYS:
            raise ValueError("Permissões desconhecidas")
        return list(dict.fromkeys(value)) if value is not None else None


class GroupInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=300)
    permission_keys: list[str] = Field(default_factory=list, max_length=30)
    company_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("permission_keys")
    @classmethod
    def validate_keys(cls, value: list[str]) -> list[str]:
        if set(value) - PERMISSION_KEYS:
            raise ValueError("Permissões inválidas")
        return list(dict.fromkeys(value))


class UserProfile(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    position: str = Field(default="", max_length=120)


class PasswordRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class TokenComplete(BaseModel):
    token: str = Field(min_length=30, max_length=250)
    password: str = Field(min_length=12, max_length=1024)


class TotpSetup(BaseModel):
    current_password: str


class TotpCode(BaseModel):
    code: str = Field(min_length=6, max_length=64)


class TotpDisable(BaseModel):
    current_password: str
    code: str = Field(min_length=6, max_length=64)


def output_user(user: User) -> dict:
    return {
        "id": user.id, "username": user.username, "full_name": user.full_name,
        "position": user.position, "role": user.role, "active": user.active,
        "organization_id": user.organization_id,
        "company_access_mode": user.company_access_mode,
        "permission_keys": user.permission_keys or [],
        "mfa_enabled": bool(user.totp_secret),
        "avatar_updated_at": user.avatar_updated_at,
    }


def audit(session: AsyncSession, user: User, action: str, entity_id: str,
          metadata: dict | None = None) -> None:
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
                           action=action, entity_id=entity_id,
                           metadata_json=metadata or {}))


async def owned_user(session: AsyncSession, actor: User, uid: str) -> User:
    target = await session.scalar(
        select(User).where(User.id == uid,
                           User.organization_id == actor.organization_id))
    if target is None:
        raise HTTPException(404, "Usuário não encontrado")
    return target


async def owned_group(session: AsyncSession, actor: User, gid: str) -> AccessGroup:
    group = await session.scalar(
        select(AccessGroup).where(AccessGroup.id == gid,
                                  AccessGroup.organization_id == actor.organization_id))
    if group is None:
        raise HTTPException(404, "Grupo não encontrado")
    return group


async def validate_links(session: AsyncSession, actor: User,
                         company_ids: list[str], group_ids: list[str]) -> None:
    if len(set(company_ids)) != len(company_ids) or len(set(group_ids)) != len(group_ids):
        raise HTTPException(422, "Associações repetidas")
    if company_ids:
        ids = set((await session.scalars(select(Company.id).where(
            Company.organization_id == actor.organization_id,
            Company.id.in_(company_ids)))).all())
        if ids != set(company_ids):
            raise HTTPException(422, "Empresas fora da organização")
    if group_ids:
        ids = set((await session.scalars(select(AccessGroup.id).where(
            AccessGroup.organization_id == actor.organization_id,
            AccessGroup.id.in_(group_ids)))).all())
        if ids != set(group_ids):
            raise HTTPException(422, "Grupos fora da organização")


async def user_links(session: AsyncSession, user: User) -> dict:
    direct = (await session.scalars(select(UserCompanyGrant.company_id)
                                     .where(UserCompanyGrant.user_id == user.id))).all()
    groups = (await session.scalars(select(GroupMembership.group_id)
                                     .where(GroupMembership.user_id == user.id))).all()
    return {"company_ids": direct, "group_ids": groups}


async def set_user_links(session: AsyncSession, user: User, *,
                         company_ids: list[str] | None = None,
                         group_ids: list[str] | None = None) -> None:
    if company_ids is not None:
        await session.execute(delete(UserCompanyGrant).where(
            UserCompanyGrant.user_id == user.id))
        session.add_all(UserCompanyGrant(user_id=user.id, company_id=cid)
                        for cid in company_ids)
    if group_ids is not None:
        await session.execute(delete(GroupMembership).where(
            GroupMembership.user_id == user.id))
        session.add_all(GroupMembership(user_id=user.id, group_id=gid)
                        for gid in group_ids)


async def set_group_companies(session: AsyncSession, group: AccessGroup,
                              company_ids: list[str]) -> None:
    await session.execute(delete(GroupCompanyGrant).where(
        GroupCompanyGrant.group_id == group.id))
    session.add_all(GroupCompanyGrant(group_id=group.id, company_id=cid)
                    for cid in company_ids)


async def group_output(session: AsyncSession, group: AccessGroup) -> dict:
    companies = (await session.scalars(
        select(GroupCompanyGrant.company_id).where(GroupCompanyGrant.group_id == group.id))).all()
    members = (await session.scalars(
        select(GroupMembership.user_id).where(GroupMembership.group_id == group.id))).all()
    return {"id": group.id, "name": group.name, "description": group.description,
            "permission_keys": group.permission_keys or [],
            "company_ids": companies, "user_ids": members}


def token_digest(token: str) -> str:
    return sha256(token.encode("ascii")).hexdigest()


async def create_account_action(session: AsyncSession, target: User,
                                purpose: str) -> str:
    token = secrets.token_urlsafe(48)
    session.add(AccountAction(
        digest=token_digest(token), user_id=target.id,
        organization_id=target.organization_id, purpose=purpose,
        expires_at=now() + timedelta(hours=48 if purpose == "invite" else 1),
    ))
    return token


def token_link(token: str, purpose: str) -> str:
    return (get_settings().app_public_url.rstrip("/") +
            "/?account_action=" + quote(purpose) + "&token=" + quote(token))


async def send_account_email(target: User, token: str, purpose: str) -> None:
    event = "Convite para Gestão Fiscal" if purpose == "invite" else "Recuperação de senha"
    link = token_link(token, purpose)
    text = ("Olá,\n\nFoi solicitado um convite para acessar a plataforma.\n"
            if purpose == "invite" else
            "Olá,\n\nFoi solicitada a recuperação de acesso à plataforma.\n")
    text += (f"Use o link seguro para definir uma nova senha:\n{link}\n\n"
             "O link expira e só pode ser usado uma vez. Se você não reconhece "
             "esta solicitação, ignore a mensagem.\n")
    await send_mail(target.username, event, text)


@router.get("/admin/permissions")
async def permission_catalog(actor: User = Depends(admin_only)) -> dict:
    return {"permissions": sorted(PERMISSION_KEYS), "roles": sorted(ROLES)}


@router.get("/admin/users")
async def list_users(actor: User = Depends(admin_only),
                     session: AsyncSession = Depends(get_session)) -> dict:
    users = (await session.scalars(select(User).where(
        User.organization_id == actor.organization_id).order_by(User.username).limit(1000))).all()
    return {"items": [output_user(item) | await user_links(session, item)
                      for item in users]}


@router.post("/admin/users", status_code=201)
async def add_user(body: UserInput, actor: User = Depends(admin_only),
                   session: AsyncSession = Depends(get_session)) -> dict:
    if body.invite and not mail_status()["ready"]:
        raise HTTPException(409, "Configure SMTP no deployer para enviar o convite")
    await validate_links(session, actor, body.company_ids, body.group_ids)
    user = User(
        organization_id=actor.organization_id, username=body.username,
        full_name=body.full_name.strip(), position=body.position.strip(),
        role=body.role, active=body.active,
        company_access_mode=body.company_access_mode,
        permission_keys=body.permission_keys,
        password_hash=hash_password(secrets.token_urlsafe(36)),
    )
    session.add(user)
    try:
        await session.flush()
        await set_user_links(session, user, company_ids=body.company_ids,
                             group_ids=body.group_ids)
        token = await create_account_action(session, user, "invite") if body.invite else None
        audit(session, actor, "user.created", user.id,
              {"role": user.role, "invited": bool(token)})
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "E-mail já cadastrado") from None
    if token:
        try:
            await send_account_email(user, token, "invite")
        except MailUnavailable:
            # User remains with unknown random password and can be reinvited.
            raise HTTPException(503, "Usuário cadastrado; falha ao enviar convite. Reenvie.") from None
    return output_user(user) | await user_links(session, user)


@router.patch("/admin/users/{uid}")
async def edit_user(uid: str, body: UserPatch, actor: User = Depends(admin_only),
                    session: AsyncSession = Depends(get_session)) -> dict:
    user = await owned_user(session, actor, uid)
    patch = body.model_dump(exclude_unset=True)
    if (uid == actor.id and
            (patch.get("active") is False or
             (patch.get("role") is not None and patch["role"] != "admin"))):
        raise HTTPException(409, "Não é permitido desativar ou rebaixar a própria conta de administração")
    await validate_links(session, actor, patch.get("company_ids") or [],
                         patch.get("group_ids") or [])
    for field in ("full_name", "position", "role", "active", "company_access_mode",
                  "permission_keys"):
        if field in patch:
            setattr(user, field, patch[field])
    if "company_ids" in patch or "group_ids" in patch:
        await set_user_links(session, user, company_ids=patch.get("company_ids"),
                             group_ids=patch.get("group_ids"))
    user.token_version += 1
    audit(session, actor, "user.updated", user.id,
          {"fields": sorted(patch.keys())})
    await session.commit()
    return output_user(user) | await user_links(session, user)


@router.delete("/admin/users/{uid}")
async def disable_user(uid: str, actor: User = Depends(admin_only),
                       session: AsyncSession = Depends(get_session)) -> dict:
    if uid == actor.id:
        raise HTTPException(409, "Não é permitido desativar a própria conta")
    user = await owned_user(session, actor, uid)
    user.active = False
    user.token_version += 1
    audit(session, actor, "user.disabled", user.id)
    await session.commit()
    return {"disabled": True}


@router.post("/admin/users/{uid}/invite")
async def reinvite_user(uid: str, actor: User = Depends(admin_only),
                        session: AsyncSession = Depends(get_session)) -> dict:
    if not mail_status()["ready"]:
        raise HTTPException(409, "SMTP não configurado")
    user = await owned_user(session, actor, uid)
    token = await create_account_action(session, user, "invite")
    audit(session, actor, "user.invited", user.id)
    await session.commit()
    try:
        await send_account_email(user, token, "invite")
    except MailUnavailable:
        raise HTTPException(503, "Convite não entregue; confira o SMTP e reenvie") from None
    return {"sent": True}


@router.get("/admin/groups")
async def list_groups(actor: User = Depends(admin_only),
                      session: AsyncSession = Depends(get_session)) -> dict:
    groups = (await session.scalars(select(AccessGroup).where(
        AccessGroup.organization_id == actor.organization_id)
        .order_by(AccessGroup.name).limit(500))).all()
    return {"items": [await group_output(session, group) for group in groups]}


@router.post("/admin/groups", status_code=201)
async def add_group(body: GroupInput, actor: User = Depends(admin_only),
                    session: AsyncSession = Depends(get_session)) -> dict:
    await validate_links(session, actor, body.company_ids, [])
    group = AccessGroup(organization_id=actor.organization_id, name=body.name.strip(),
                        description=body.description.strip(),
                        permission_keys=body.permission_keys)
    session.add(group)
    try:
        await session.flush()
        await set_group_companies(session, group, body.company_ids)
        audit(session, actor, "group.created", group.id)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Já existe grupo com esse nome") from None
    return await group_output(session, group)


@router.put("/admin/groups/{gid}")
async def edit_group(gid: str, body: GroupInput, actor: User = Depends(admin_only),
                     session: AsyncSession = Depends(get_session)) -> dict:
    group = await owned_group(session, actor, gid)
    await validate_links(session, actor, body.company_ids, [])
    group.name = body.name.strip()
    group.description = body.description.strip()
    group.permission_keys = body.permission_keys
    try:
        await set_group_companies(session, group, body.company_ids)
        members = (await session.scalars(select(User).join(
            GroupMembership, GroupMembership.user_id == User.id).where(
            GroupMembership.group_id == gid,
            User.organization_id == actor.organization_id))).all()
        for user in members:
            user.token_version += 1
        audit(session, actor, "group.updated", group.id)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "Nome de grupo já utilizado") from None
    return await group_output(session, group)


@router.delete("/admin/groups/{gid}")
async def remove_group(gid: str, actor: User = Depends(admin_only),
                       session: AsyncSession = Depends(get_session)) -> dict:
    group = await owned_group(session, actor, gid)
    members = (await session.scalars(select(User).join(
        GroupMembership, GroupMembership.user_id == User.id).where(
        GroupMembership.group_id == gid,
        User.organization_id == actor.organization_id))).all()
    for user in members:
        user.token_version += 1
    await session.execute(delete(GroupCompanyGrant).where(GroupCompanyGrant.group_id == gid))
    await session.execute(delete(GroupMembership).where(GroupMembership.group_id == gid))
    await session.delete(group)
    audit(session, actor, "group.deleted", gid)
    await session.commit()
    return {"deleted": True}


@router.get("/admin/mail/status")
async def mail_configuration_status(actor: User = Depends(admin_only)) -> dict:
    return mail_status()


@router.post("/admin/mail/test")
async def test_email(actor: User = Depends(admin_only)) -> dict:
    try:
        await send_mail(actor.username, "Teste SMTP Gestão Fiscal",
                        "Este e-mail confirma a entrega SMTP da sua instalação.")
    except MailUnavailable:
        raise HTTPException(503, "SMTP indisponível; confira parâmetros de envio") from None
    return {"sent": True, "recipient": actor.username}


@router.post("/auth/password-reset/request", status_code=202)
async def request_reset(body: PasswordRequest, session: AsyncSession = Depends(get_session)) -> dict:
    # Always return the same response to prevent account enumeration.
    generic = {"accepted": True, "message": "Se a conta existir, uma mensagem será enviada."}
    if not mail_status()["ready"]:
        return generic
    user = await session.scalar(select(User).where(
        User.username == body.email.strip().lower(), User.active.is_(True)))
    if user is None:
        return generic
    recent = await session.scalar(select(AccountAction.digest).where(
        AccountAction.user_id == user.id, AccountAction.purpose == "reset",
        AccountAction.created_at >= now() - timedelta(minutes=5)).limit(1))
    if recent:
        return generic
    token = await create_account_action(session, user, "reset")
    await session.commit()
    try:
        await send_account_email(user, token, "reset")
    except MailUnavailable:
        pass  # Public response remains constant; operators inspect SMTP diagnostics.
    return generic


@router.post("/auth/token/complete")
async def complete_token(body: TokenComplete,
                         session: AsyncSession = Depends(get_session)) -> dict:
    try:
        digest = token_digest(body.token)
    except UnicodeEncodeError:
        raise HTTPException(400, "Token inválido") from None
    action = await session.scalar(select(AccountAction).where(
        AccountAction.digest == digest).with_for_update())
    if (action is None or action.purpose not in {"invite", "reset"}
            or action.consumed_at is not None or
            (action.expires_at.replace(tzinfo=timezone.utc) if action.expires_at.tzinfo is None
             else action.expires_at) <= now()):
        raise HTTPException(400, "Token inválido ou expirado")
    user = await session.scalar(select(User).where(
        User.id == action.user_id, User.organization_id == action.organization_id)
        .with_for_update())
    if user is None:
        raise HTTPException(400, "Token inválido ou expirado")
    user.password_hash = hash_password(body.password)
    user.active = True
    user.token_version += 1
    action.consumed_at = now()
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
                           action="auth." + action.purpose + ".completed", entity_id=user.id))
    await session.commit()
    return {"updated": True}


@router.patch("/profile")
async def edit_my_profile(body: UserProfile, user: User = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> dict:
    user.full_name = body.full_name.strip()
    user.position = body.position.strip()
    audit(session, user, "user.profile.updated", user.id)
    await session.commit()
    return output_user(user)


@router.post("/profile/photo")
async def upload_photo(file: UploadFile = File(...), user: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)) -> dict:
    if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(415, "Foto deve ser PNG, JPEG ou WEBP")
    data = await file.read(MAX_PHOTO + 1)
    if len(data) > MAX_PHOTO:
        raise HTTPException(413, "Foto acima de 1 MiB")
    try:
        image = Image.open(BytesIO(data))
        image.verify()
        image = Image.open(BytesIO(data))
        if image.width > 3000 or image.height > 3000:
            raise ValueError("Imagem muito grande")
        picture = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"), (256, 256))
        output = BytesIO()
        picture.save(output, format="WEBP", quality=85, method=4)
        encoded = output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError):
        raise HTTPException(422, "Arquivo de foto inválido") from None
    user.avatar = encoded
    user.avatar_updated_at = now()
    audit(session, user, "user.photo.updated", user.id)
    await session.commit()
    return {"updated": True, "avatar_updated_at": user.avatar_updated_at}


@router.get("/profile/photo/{uid}")
async def view_photo(uid: str, user: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)) -> RawResponse:
    if uid != user.id and user.role != "admin":
        raise HTTPException(403, "Foto de outro usuário não autorizada")
    target = await owned_user(session, user, uid)
    if not target.avatar:
        raise HTTPException(404, "Foto não cadastrada")
    return RawResponse(target.avatar, media_type="image/webp",
                       headers={"Cache-Control": "private, no-store",
                                "X-Content-Type-Options": "nosniff"})


@router.post("/profile/2fa/setup")
async def setup_2fa(body: TotpSetup, user: User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)) -> dict:
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(403, "Senha incorreta")
    if user.totp_secret:
        raise HTTPException(409, "2FA já habilitado; desative antes de cadastrar outro dispositivo")
    seed = new_seed()
    user.totp_pending = encrypt_seed(seed)
    audit(session, user, "auth.2fa.enrollment.started", user.id)
    await session.commit()
    return {"secret": seed, "otpauth_uri": enrollment_uri(user.username, seed)}


@router.post("/profile/2fa/confirm")
async def confirm_2fa(body: TotpCode, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> dict:
    if not user.totp_pending or user.totp_secret:
        raise HTTPException(409, "Não existe configuração 2FA pendente")
    seed = decrypt_seed(user.totp_pending)
    counter = valid_counter(seed, body.code)
    if counter is None:
        raise HTTPException(422, "Código do autenticador inválido")
    codes, hashed = recovery_codes()
    user.totp_secret = user.totp_pending
    user.totp_pending = None
    user.totp_last_counter = counter
    user.totp_recovery_hashes = hashed
    user.token_version += 1
    audit(session, user, "auth.2fa.enabled", user.id)
    await session.commit()
    return {"enabled": True, "recovery_codes": codes,
            "note": "Guarde esses códigos em local seguro. Eles não serão exibidos novamente."}


@router.post("/profile/2fa/disable")
async def disable_2fa(body: TotpDisable, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)) -> dict:
    if not user.totp_secret:
        raise HTTPException(409, "2FA não habilitado")
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(403, "Senha incorreta")
    counter = valid_counter(decrypt_seed(user.totp_secret), body.code,
                            previous_counter=user.totp_last_counter)
    recovered, remaining = recover(body.code, user.totp_recovery_hashes or [])
    if counter is None and not recovered:
        raise HTTPException(403, "Código do autenticador ou recuperação inválido")
    user.totp_secret = None
    user.totp_pending = None
    user.totp_recovery_hashes = []
    user.totp_last_counter = 0
    user.token_version += 1
    audit(session, user, "auth.2fa.disabled", user.id)
    await session.commit()
    return {"enabled": False}


@router.get("/companies/cnpj/{cnpj}/lookup")
async def company_lookup(cnpj: str, actor: User = Depends(admin_only)) -> dict:
    if not get_settings().cnpj_lookup_enabled:
        raise HTTPException(409, "Consulta cadastral pública desabilitada")
    normalized = "".join(ch for ch in cnpj if ch.isascii() and ch.isdigit())
    if len(normalized) != 14 or not valid_cnpj(normalized):
        raise HTTPException(422, "CNPJ numérico inválido; CNPJ alfanumérico requer cadastro manual")
    # Fixed provider endpoint; no user-provided host or arbitrary redirects (SSRF).
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get("https://brasilapi.com.br/api/cnpj/v1/" + normalized)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Consulta cadastral indisponível; preencha manualmente") from None
    if not isinstance(data, dict) or str(data.get("cnpj", "")).replace(".", "").replace("/", "").replace("-", "") != normalized:
        raise HTTPException(502, "Resposta cadastral inconsistente")
    return {
        "cnpj": normalized,
        "name": str(data.get("razao_social") or "")[:180],
        "trade_name": str(data.get("nome_fantasia") or "")[:180],
        "status": str(data.get("descricao_situacao_cadastral") or "")[:100],
        "state": str(data.get("uf") or "")[:2],
        "city": str(data.get("municipio") or "")[:160],
        "address": str(data.get("logradouro") or "")[:200],
        "source": "brasilapi_publica",
        "warning": "Consulta cadastral pública de terceiros; não constitui confirmação oficial da Receita.",
    }
