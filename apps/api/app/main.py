from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from importlib.metadata import version
import hmac
import json
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query as QueryParam, Request, Response
from fastapi.responses import Response as RawResponse
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import AuditEvent, Company, Query, Record, User, now
from app.schemas import CompanyIn, LoginIn, PasswordChangeIn, QueryIn, ResolveUncertainIn
from app.security import admin_only, current_user, hash_password, issue_token, verify_password, webhook_token_hash
from app.storage import BlobStore
from app.documents import router as documents_router
from app.reconciliation_api import router as reconciliation_router
from app.finance_controls import router as finance_controls_router
from app.branding import router as branding_router
from app.central_reforma_api import router as central_reforma_router
from app.tax_calculator_api import router as tax_calculator_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().validate_security()
    yield


app = FastAPI(title="Plataforma Fiscal",
              version=version("fiscal-assist-api"), lifespan=lifespan)
app.include_router(documents_router)
app.include_router(reconciliation_router)
app.include_router(finance_controls_router)
app.include_router(branding_router)
app.include_router(central_reforma_router)
app.include_router(tax_calculator_router)


def capabilities() -> dict:
    s = get_settings()
    if s.provider_mode == "disabled":
        return {
            "provider_mode": "disabled", "environments": [],
            "resources": {name: {"enabled": False,
                "reason": "Integração CBS ainda não homologada neste modo"}
                for name in ("debitos", "creditos", "pagamentos", "recolhimentos")},
            "ibs": {"enabled": False, "reason": "Contrato IBS separado, ainda não homologado"},
        }
    real = s.provider_mode == "receita"
    environments = ["fixture"] if not real else (["restricted"] if s.cbs_restricted_enabled else []) + (["production"] if s.cbs_production_enabled else [])
    return {
        "provider_mode": s.provider_mode,
        "environments": environments,
        "resources": {name: {
            "enabled": not real or ((name in {"debitos", "creditos"}) or s.cbs_payments_enabled),
            "reason": "Disponibilidade e credenciais oficiais devem ser homologadas" if real and name in {"pagamentos", "recolhimentos"} and not s.cbs_payments_enabled else None,
        } for name in ("debitos", "creditos", "pagamentos", "recolhimentos")},
        "ibs": {"enabled": False, "reason": "Contrato IBS separado, ainda não homologado"},
    }


def query_out(value: Query) -> dict:
    return {"id": value.id, "company_id": value.company_id, "resource": value.resource,
            "environment": value.environment, "status": value.status,
            "ticket": value.ticket, "raw_sha256": value.raw_sha256, "error": value.error,
            "poll_count": value.poll_count, "attempts": value.poll_count,
            "next_poll_at": value.next_poll_at,
            "opening_started_at": value.opening_started_at,
            "created_at": value.created_at, "updated_at": value.updated_at}


def record_out(value: Record) -> dict:
    return {"id": value.id, "query_id": value.query_id, "company_id": value.company_id,
            "resource": value.resource, "period": value.period, "source_key": value.source_key,
            "amount": str(value.amount) if value.amount is not None else None,
            "principal": str(value.principal) if value.principal is not None else None,
            "penalty": str(value.penalty) if value.penalty is not None else None,
            "interest": str(value.interest) if value.interest is not None else None,
            "credit_to_appropriate": str(value.credit_to_appropriate) if value.credit_to_appropriate is not None else None,
            "credit_appropriated": str(value.credit_appropriated) if value.credit_appropriated is not None else None,
            "credit_used": str(value.credit_used) if value.credit_used is not None else None,
            "credit_category": value.credit_category, "provenance": value.provenance,
            "raw": value.raw, "created_at": value.created_at}


def redis_client() -> Redis:
    s = get_settings()
    return Redis(host=s.redis_host, port=6379, password=s.redis_password or None, socket_connect_timeout=2, decode_responses=True)


async def throttle_login(username: str, session: AsyncSession) -> None:
    if get_settings().self_hosted:
        timestamp = int(time.time())
        try:
            # SQLite commits the cleanup and attempt atomically. Each username
            # has a fifteen-minute window that survives service restarts.
            await session.execute(text("DELETE FROM auth_attempts WHERE window_end < :cutoff"),
                                  {"cutoff": timestamp - 3600})
            result = await session.execute(text("""
                INSERT INTO auth_attempts (username_hash, attempts, window_end)
                VALUES (:username_hash, 1, :window_end)
                ON CONFLICT(username_hash) DO UPDATE SET
                  attempts = CASE WHEN auth_attempts.window_end <= :timestamp
                                  THEN 1 ELSE auth_attempts.attempts + 1 END,
                  window_end = CASE WHEN auth_attempts.window_end <= :timestamp
                                    THEN :window_end ELSE auth_attempts.window_end END
                RETURNING attempts
            """), {"username_hash": sha256(username.strip().lower().encode()).hexdigest(),
                   "timestamp": timestamp, "window_end": timestamp + 900})
            attempts = result.scalar_one()
            await session.commit()
        except Exception:
            await session.rollback()
            raise HTTPException(503, "Limite de autenticação indisponível") from None
        if attempts > 10:
            raise HTTPException(429, "Tente novamente mais tarde")
        return
    redis = redis_client()
    key = "auth:attempt:" + sha256(username.strip().lower().encode()).hexdigest()
    try:
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, 900)
    except Exception:
        raise HTTPException(503, "Limite de autenticação indisponível") from None
    finally:
        await redis.aclose()
    if attempts > 10:
        raise HTTPException(429, "Tente novamente mais tarde")


@app.get("/health/live")
async def live() -> dict:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(session: AsyncSession = Depends(get_session)) -> dict:
    try:
        await session.execute(text("SELECT 1"))
        if get_settings().self_hosted:
            if await session.scalar(text("SELECT 1 FROM users LIMIT 1")) is None:
                raise RuntimeError("Primeiro administrador não configurado")
            await session.execute(text("SELECT 1 FROM auth_attempts LIMIT 1"))
            return {"status": "ready"}
        redis = redis_client()
        try:
            await redis.ping()
        finally:
            await redis.aclose()
    except Exception:
        raise HTTPException(503, "Dependência indisponível") from None
    return {"status": "ready"}


@app.post("/api/v1/auth/login")
async def login(body: LoginIn, session: AsyncSession = Depends(get_session)) -> dict:
    username = body.username.strip().lower()
    await throttle_login(username, session)
    user = await session.scalar(select(User).where(User.username == username, User.active.is_(True)))
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(401, "Credenciais inválidas")
    return {"access_token": issue_token(user), "token_type": "bearer"}


@app.get("/api/v1/auth/me")
async def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "username": user.username, "role": user.role,
            "organization_id": user.organization_id}


@app.post("/api/v1/auth/change-password")
async def change_password(body: PasswordChangeIn, user: User = Depends(current_user),
                          session: AsyncSession = Depends(get_session)) -> dict:
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(403, "Senha atual inválida")
    if body.current_password == body.new_password:
        raise HTTPException(422, "A nova senha deve ser diferente")
    locked = await session.scalar(select(User).where(User.id == user.id,
        User.organization_id == user.organization_id).with_for_update())
    locked.password_hash = hash_password(body.new_password)
    locked.token_version += 1
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
        action="auth.password.changed", entity_id=user.id))
    await session.commit()
    return {"changed": True, "access_token": issue_token(locked), "token_type": "bearer"}


@app.get("/api/v1/capabilities")
async def get_capabilities(user: User = Depends(current_user)) -> dict:
    return capabilities()


@app.get("/api/v1/companies")
async def companies(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    values = (await session.scalars(select(Company).where(Company.organization_id == user.organization_id)
                                    .order_by(Company.name, Company.id).limit(500))).all()
    return {"items": [{"id": c.id, "name": c.name, "cnpj": c.cnpj,
                        "cnpj_base": c.cnpj_base} for c in values], "next_cursor": None}


@app.post("/api/v1/companies", status_code=201)
async def create_company(body: CompanyIn, user: User = Depends(admin_only),
                         session: AsyncSession = Depends(get_session)) -> dict:
    company = Company(organization_id=user.organization_id, name=body.name,
                      cnpj=body.cnpj, cnpj_base=body.cnpj[:8])
    session.add(company)
    try:
        await session.flush()
        session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
                               action="company.created", entity_id=company.id))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "CNPJ já cadastrado nesta organização") from None
    return {"id": company.id, "name": company.name, "cnpj": company.cnpj,
            "cnpj_base": company.cnpj_base}


def check_capability(payload: QueryIn, org_id: str, cnpj_base: str) -> None:
    s = get_settings()
    caps = capabilities()
    if payload.environment not in caps["environments"] or not caps["resources"][payload.resource]["enabled"]:
        raise HTTPException(409, "Ambiente ou recurso desabilitado")
    if payload.environment != "fixture" and (org_id != s.cbs_credential_organization_id
                                                or cnpj_base not in s.allowed_bases):
        raise HTTPException(403, "Credencial não habilitada para organização/CNPJ base")


@app.post("/api/v1/queries", status_code=202)
async def create_query(body: QueryIn, idempotency_key: str = Header(min_length=8, max_length=128),
                       user: User = Depends(admin_only),
                       session: AsyncSession = Depends(get_session)) -> dict:
    fingerprint = sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
    # The lookup precedes capability checks so a repeated request returns its
    # original result even after the feature flag is switched off.
    existing = await session.scalar(select(Query).where(Query.organization_id == user.organization_id,
                                                         Query.idempotency_key == idempotency_key))
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(409, "Idempotency-Key reutilizada com payload diferente")
        return query_out(existing)
    company = await session.scalar(select(Company).where(Company.id == body.company_id,
                                                         Company.organization_id == user.organization_id).with_for_update())
    if company is None:
        raise HTTPException(404, "Empresa não encontrada")
    # A concurrent identical request may have committed while this request
    # waited for the company lock.
    existing = await session.scalar(select(Query).where(Query.organization_id == user.organization_id,
                                                         Query.idempotency_key == idempotency_key))
    if existing:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(409, "Idempotency-Key reutilizada com payload diferente")
        return query_out(existing)
    check_capability(body, user.organization_id, company.cnpj_base)
    if get_settings().self_hosted and body.environment != "fixture":
        branches = await session.scalar(select(func.count()).select_from(Company).where(
            Company.organization_id == user.organization_id,
            Company.cnpj_base == company.cnpj_base))
        if branches != 1:
            raise HTTPException(409, "CBS consulta a raiz CNPJ; concilie filiais antes da consulta oficial")
    active = await session.scalar(select(Query.id).where(Query.cnpj_base == company.cnpj_base,
        Query.organization_id == user.organization_id,
        Query.environment == body.environment,
        Query.status.in_(["queued", "opening", "awaiting", "processing", "uncertain"])).limit(1))
    if active:
        raise HTTPException(409, "Já existe consulta em andamento para esta raiz CNPJ e ambiente")
    if body.environment != "fixture":
        opened = await session.scalar(select(func.count()).select_from(Query).where(
            Query.cnpj_base == company.cnpj_base, Query.organization_id == user.organization_id,
            Query.environment == body.environment,
            Query.opening_started_at >= now() - timedelta(hours=24)))
        if opened >= 4:
            raise HTTPException(429, "Limite conservador de quatro aberturas em 24 horas")
    request = Query(organization_id=user.organization_id, company_id=company.id,
                    cnpj_base=company.cnpj_base,
                    resource=body.resource, environment=body.environment,
                    idempotency_key=idempotency_key, request_fingerprint=fingerprint,
                    webhook_secret_hash="pending", status="queued", next_poll_at=now())
    session.add(request)
    await session.flush()
    request.webhook_secret_hash = webhook_token_hash(request.id)
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
                           action="query.created", entity_id=request.id,
                           metadata_json={"resource": body.resource, "environment": body.environment}))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(select(Query).where(Query.organization_id == user.organization_id,
                                                             Query.idempotency_key == idempotency_key))
        if existing and existing.request_fingerprint == fingerprint:
            return query_out(existing)
        raise HTTPException(409, "Consulta duplicada") from None
    return query_out(request)


def cursor_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    if not cursor.isdecimal() or int(cursor) > 1_000_000:
        raise HTTPException(422, "Cursor inválido")
    return int(cursor)


@app.get("/api/v1/queries")
async def list_queries(company_id: str | None = None, cursor: str | None = None,
                       limit: int = QueryParam(50, ge=1, le=200),
                       user: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)) -> dict:
    offset = cursor_offset(cursor)
    stmt = select(Query).where(Query.organization_id == user.organization_id)
    if company_id:
        stmt = stmt.where(Query.company_id == company_id)
    values = (await session.scalars(stmt.order_by(Query.created_at.desc(), Query.id.desc())
                                    .offset(offset).limit(limit + 1))).all()
    return {"items": [query_out(x) for x in values[:limit]],
            "next_cursor": str(offset + limit) if len(values) > limit else None}


@app.get("/api/v1/queries/{query_id}")
async def get_query(query_id: str, user: User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)) -> dict:
    value = await session.scalar(select(Query).where(Query.id == query_id,
                                                     Query.organization_id == user.organization_id))
    if value is None:
        raise HTTPException(404, "Consulta não encontrada")
    return query_out(value)


@app.post("/api/v1/queries/{query_id}/resolve")
async def resolve_uncertain(query_id: str, body: ResolveUncertainIn,
                            user: User = Depends(admin_only),
                            session: AsyncSession = Depends(get_session)) -> dict:
    value = await session.scalar(select(Query).where(Query.id == query_id,
        Query.organization_id == user.organization_id).with_for_update())
    if value is None:
        raise HTTPException(404, "Consulta não encontrada")
    if value.status != "uncertain":
        raise HTTPException(409, "Consulta não está aguardando conciliação manual")
    if body.action == "attach_ticket":
        if not body.ticket:
            raise HTTPException(422, "Informe o tíquete confirmado no ambiente oficial")
        value.ticket = body.ticket
        value.status = "awaiting"
        value.next_poll_at = now()
    else:
        value.status = "failed"
        value.next_poll_at = None
    value.error = None
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
        action="query.uncertain.resolved", entity_id=value.id,
        metadata_json={"action": body.action, "note": body.note, "ticket": value.ticket}))
    await session.commit()
    return query_out(value)


@app.get("/api/v1/queries/{query_id}/raw")
async def get_raw(query_id: str, user: User = Depends(current_user),
                  session: AsyncSession = Depends(get_session)) -> RawResponse:
    value = await session.scalar(select(Query).where(Query.id == query_id,
                                                     Query.organization_id == user.organization_id))
    if value is None or not value.raw_key or not value.raw_sha256:
        raise HTTPException(404, "Arquivo bruto não encontrado")
    try:
        data = await BlobStore(get_settings()).get(value.raw_key, value.raw_sha256)
    except Exception:
        raise HTTPException(503, "Arquivo bruto indisponível ou sem integridade") from None
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
                           action="raw.downloaded", entity_id=query_id))
    await session.commit()
    return RawResponse(data, media_type="application/json",
                       headers={"X-Content-SHA256": value.raw_sha256, "Cache-Control": "no-store"})


@app.get("/api/v1/records")
async def records(company_id: str | None = None, resource: str | None = None,
                  query_id: str | None = None, cursor: str | None = None,
                  limit: int = QueryParam(50, ge=1, le=200),
                  user: User = Depends(current_user),
                  session: AsyncSession = Depends(get_session)) -> dict:
    offset = cursor_offset(cursor)
    stmt = select(Record).where(Record.organization_id == user.organization_id)
    if company_id:
        stmt = stmt.where(Record.company_id == company_id)
    if resource:
        stmt = stmt.where(Record.resource == resource)
    if query_id:
        stmt = stmt.where(Record.query_id == query_id)
    values = (await session.scalars(stmt.order_by(Record.created_at.desc(), Record.id.desc())
                                    .offset(offset).limit(limit + 1))).all()
    return {"items": [record_out(x) for x in values[:limit]],
            "next_cursor": str(offset + limit) if len(values) > limit else None}


@app.get("/api/v1/coverage")
async def coverage(company_id: str, environment: str = "fixture",
                   user: User = Depends(current_user),
                   session: AsyncSession = Depends(get_session)) -> dict:
    company = await session.scalar(select(Company).where(Company.id == company_id,
        Company.organization_id == user.organization_id))
    if company is None:
        raise HTTPException(404, "Empresa não encontrada")
    if environment not in capabilities()["environments"]:
        raise HTTPException(422, "Ambiente desabilitado")
    resources = {}
    for resource in ("debitos", "creditos", "pagamentos", "recolhimentos"):
        last = await session.scalar(select(Query).where(
            Query.organization_id == user.organization_id,
            Query.cnpj_base == company.cnpj_base,
            Query.environment == environment, Query.resource == resource,
            Query.status == "completed").order_by(Query.opening_started_at.desc(), Query.id.desc()).limit(1))
        previous = last.opening_started_at or last.created_at if last else None
        if previous and previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        age = round((now() - previous).total_seconds() / 86400, 2) if previous else None
        resources[resource] = {
            "last_completed_query_id": last.id if last else None,
            "last_completed_opened_at": previous,
            "age_days": age,
            "needs_attention": age is None or age >= 7,
            "gap_over_eight_days": age is not None and age > 8,
            "first_query_limited": last is None,
        }
    return {"company_id": company.id, "cnpj_base": company.cnpj_base,
            "environment": environment, "incremental_max_days": 8,
            "has_automatic_backfill": False, "resources": resources}


async def check_webhook(query_id: str, secret: str, session: AsyncSession) -> Query:
    value = await session.get(Query, query_id)
    token_hash = sha256(secret.encode()).hexdigest()
    if value is None or not hmac.compare_digest(value.webhook_secret_hash, token_hash):
        raise HTTPException(404, "Webhook não encontrado")
    return value


@app.head("/api/v1/webhooks/cbs/{query_id}/{secret}")
async def webhook_head(query_id: str, secret: str,
                       session: AsyncSession = Depends(get_session)) -> Response:
    await check_webhook(query_id, secret, session)
    return Response(status_code=200)


@app.post("/api/v1/webhooks/cbs/{query_id}/{secret}", status_code=202)
async def webhook_post(query_id: str, secret: str, request: Request,
                       session: AsyncSession = Depends(get_session)) -> dict:
    value = await check_webhook(query_id, secret, session)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 8192:
            raise HTTPException(413, "Notificação grande demais")
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(400, "Notificação inválida") from None
    if not isinstance(payload, dict) or value.ticket is None or payload.get("tiqueteSolicitacao") != value.ticket:
        raise HTTPException(409, "Tíquete não confirmado; nova tentativa necessária")
    # No signed URL is accepted or persisted from unauthenticated body. The
    # worker verifies the status through OAuth before fetching the file.
    if value.status in {"awaiting", "processing"}:
        value.next_poll_at = now()
        session.add(AuditEvent(organization_id=value.organization_id, actor="webhook",
            action="webhook.accepted", entity_id=query_id,
            metadata_json={"ticket": value.ticket}))
        await session.commit()
    return {"accepted": True}
