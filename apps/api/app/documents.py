"""Company-scoped upload agents and evidence-preserving fiscal XML ingestion."""

from __future__ import annotations

from hashlib import sha256
import hmac
import re
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import Response as RawResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import AgentCredential, AuditEvent, Company, FiscalDocument, User, now
from app.security import admin_only, bearer, current_user
from app.storage import BlobStore
from app.xml_parser import parse_fiscal_xml


router = APIRouter(prefix="/api/v1")
MAX_XML_BYTES = 10 * 1024 * 1024
AGENT_PREFIX = "rta_"


class AgentIn(BaseModel):
    company_id: str = Field(min_length=36, max_length=36)
    name: str = Field(min_length=2, max_length=120)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        result = value.strip()
        if len(result) < 2:
            raise ValueError("Nome do agente precisa de dois caracteres")
        return result


def agent_out(value: AgentCredential) -> dict:
    return {"id": value.id, "company_id": value.company_id, "name": value.label,
            "created_at": value.created_at, "revoked_at": value.revoked_at,
            "last_used_at": value.last_used_at}


def document_out(value: FiscalDocument) -> dict:
    return {"id": value.id, "company_id": value.company_id, "kind": value.kind,
            "document_key": value.document_key, "model": value.model,
            "issuer_cnpj": value.issuer_cnpj, "recipient_cnpj": value.recipient_cnpj,
            "sha256": value.sha256, "size_bytes": value.size_bytes,
            "period": value.period, "issue_at": value.issue_at, "direction": value.direction,
            "total_amount": str(value.total_amount) if value.total_amount is not None else None,
            "cbs_amount": str(value.cbs_amount) if value.cbs_amount is not None else None,
            "ibs_amount": str(value.ibs_amount) if value.ibs_amount is not None else None,
            "signature_present": value.signature_present,
            "authorization_status": value.authorization_status,
            "created_at": value.created_at}


@router.post("/agents", status_code=201)
async def create_agent(body: AgentIn, user: User = Depends(admin_only),
                       session: AsyncSession = Depends(get_session)) -> dict:
    company = await session.scalar(select(Company).where(Company.id == body.company_id,
                                                         Company.organization_id == user.organization_id))
    if company is None:
        raise HTTPException(404, "Empresa não encontrada")
    secret = AGENT_PREFIX + secrets.token_urlsafe(48)
    credential = AgentCredential(organization_id=user.organization_id,
                                 company_id=company.id, label=body.name.strip(),
                                 token_hash=sha256(secret.encode()).hexdigest())
    session.add(credential)
    await session.flush()
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
        action="agent.created", entity_id=credential.id,
        metadata_json={"company_id": company.id}))
    await session.commit()
    # The token is never stored as plaintext or returned by any subsequent API.
    return agent_out(credential) | {"token": secret}


@router.get("/agents")
async def list_agents(company_id: str | None = None, user: User = Depends(admin_only),
                      session: AsyncSession = Depends(get_session)) -> dict:
    stmt = select(AgentCredential).where(AgentCredential.organization_id == user.organization_id)
    if company_id:
        stmt = stmt.where(AgentCredential.company_id == company_id)
    values = (await session.scalars(stmt.order_by(AgentCredential.created_at.desc(),
                                                  AgentCredential.id.desc()).limit(500))).all()
    return {"items": [agent_out(item) for item in values], "next_cursor": None}


@router.delete("/agents/{agent_id}")
async def revoke_agent(agent_id: str, user: User = Depends(admin_only),
                       session: AsyncSession = Depends(get_session)) -> dict:
    credential = await session.scalar(select(AgentCredential).where(
        AgentCredential.id == agent_id, AgentCredential.organization_id == user.organization_id
    ).with_for_update())
    if credential is None:
        raise HTTPException(404, "Agente não encontrado")
    if credential.revoked_at is None:
        credential.revoked_at = now()
        session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
            action="agent.revoked", entity_id=credential.id))
        await session.commit()
    return {"id": credential.id, "revoked": True}


async def ingest_identity(company_id: str,
                          credentials: HTTPAuthorizationCredentials | None,
                          session: AsyncSession) -> tuple[str, str, AgentCredential | None]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Autenticação requerida", headers={"WWW-Authenticate": "Bearer"})
    token = credentials.credentials
    if token.startswith(AGENT_PREFIX):
        digest = sha256(token.encode()).hexdigest()
        agent = await session.scalar(select(AgentCredential).where(
            AgentCredential.token_hash == digest, AgentCredential.revoked_at.is_(None)
        ).with_for_update())
        if agent is None or not hmac.compare_digest(agent.token_hash, digest):
            raise HTTPException(401, "Token inválido")
        if agent.company_id != company_id:
            raise HTTPException(403, "Agente sem acesso à empresa")
        return agent.organization_id, f"agent:{agent.id}", agent
    user = await current_user(credentials, session)
    if user.role != "admin":
        raise HTTPException(403, "Privilégio de administração requerido")
    return user.organization_id, user.id, None


async def _read_xml(request: Request) -> bytes:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/xml" or request.headers.get("content-encoding", "identity") != "identity":
        raise HTTPException(415, "Envie XML não comprimido com Content-Type application/xml")
    length = request.headers.get("content-length")
    if length and length.isdecimal() and int(length) > MAX_XML_BYTES:
        raise HTTPException(413, "XML excede o limite de 10 MiB")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_XML_BYTES:
            raise HTTPException(413, "XML excede o limite de 10 MiB")
        chunks.append(chunk)
    if not size:
        raise HTTPException(422, "XML vazio")
    return b"".join(chunks)


@router.post("/documents/ingest", status_code=201)
async def ingest_document(request: Request, response: Response,
                          company_id: str = Query(min_length=36, max_length=36),
                          x_content_sha256: str = Header(min_length=64, max_length=64),
                          credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
                          session: AsyncSession = Depends(get_session)) -> dict:
    organization_id, actor, agent = await ingest_identity(company_id, credentials, session)
    company = await session.scalar(select(Company).where(Company.id == company_id,
        Company.organization_id == organization_id))
    if company is None:
        raise HTTPException(404, "Empresa não encontrada")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", x_content_sha256):
        raise HTTPException(422, "X-Content-SHA256 inválido")
    data = await _read_xml(request)
    digest = sha256(data).hexdigest()
    if not hmac.compare_digest(digest, x_content_sha256.lower()):
        raise HTTPException(422, "X-Content-SHA256 divergente")
    existing = await session.scalar(select(FiscalDocument).where(
        FiscalDocument.organization_id == organization_id,
        FiscalDocument.company_id == company.id, FiscalDocument.sha256 == digest))
    if existing is not None:
        if agent:
            agent.last_used_at = now()
            await session.commit()
        response.status_code = 200
        return document_out(existing) | {"duplicate": True}
    try:
        metadata = parse_fiscal_xml(data, company.cnpj)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None

    store = BlobStore(get_settings())
    try:
        await store.ensure_bucket()
        raw_key, stored_digest = await store.put_document(organization_id, company.id, data)
    except Exception:
        raise HTTPException(503, "Armazenamento fiscal indisponível") from None
    if stored_digest != digest:
        raise HTTPException(503, "Falha de integridade do armazenamento fiscal")

    document = FiscalDocument(organization_id=organization_id, company_id=company.id,
        agent_id=agent.id if agent else None, sha256=digest, raw_key=raw_key,
        size_bytes=len(data), kind=metadata.kind, document_key=metadata.document_key,
        model=metadata.model, issuer_cnpj=metadata.issuer_cnpj,
        recipient_cnpj=metadata.recipient_cnpj, period=metadata.period,
        issue_at=metadata.issue_at,
        direction=metadata.direction, total_amount=metadata.total_amount,
        cbs_amount=metadata.cbs_amount, ibs_amount=metadata.ibs_amount,
        signature_present=metadata.signature_present,
        authorization_status=metadata.authorization_status)
    session.add(document)
    try:
        await session.flush()
        if agent:
            agent.last_used_at = now()
        session.add(AuditEvent(organization_id=organization_id, actor=actor,
            action="document.ingested", entity_id=document.id,
            metadata_json={"company_id": company.id, "sha256": digest, "kind": metadata.kind}))
        await session.commit()
    except IntegrityError:
        # One concurrent upload won the unique (organization, company, hash) key.
        await session.rollback()
        existing = await session.scalar(select(FiscalDocument).where(
            FiscalDocument.organization_id == organization_id,
            FiscalDocument.company_id == company.id, FiscalDocument.sha256 == digest))
        if existing is not None:
            response.status_code = 200
            return document_out(existing) | {"duplicate": True}
        raise HTTPException(409, "Conflito ao registrar XML") from None
    return document_out(document) | {"duplicate": False}


@router.get("/documents")
async def list_documents(company_id: str | None = None, cursor: str | None = None,
                         limit: int = Query(50, ge=1, le=200),
                         user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)) -> dict:
    if cursor is not None and (not cursor.isdecimal() or int(cursor) > 1_000_000):
        raise HTTPException(422, "Cursor inválido")
    offset = int(cursor or "0")
    stmt = select(FiscalDocument).where(FiscalDocument.organization_id == user.organization_id)
    if company_id:
        stmt = stmt.where(FiscalDocument.company_id == company_id)
    values = (await session.scalars(stmt.order_by(FiscalDocument.created_at.desc(),
        FiscalDocument.id.desc()).offset(offset).limit(limit + 1))).all()
    return {"items": [document_out(item) for item in values[:limit]],
            "next_cursor": str(offset + limit) if len(values) > limit else None}


@router.get("/documents/{document_id}/raw")
async def download_document(document_id: str, user: User = Depends(current_user),
                            session: AsyncSession = Depends(get_session)) -> RawResponse:
    document = await session.scalar(select(FiscalDocument).where(
        FiscalDocument.id == document_id, FiscalDocument.organization_id == user.organization_id))
    if document is None:
        raise HTTPException(404, "Documento não encontrado")
    try:
        data = await BlobStore(get_settings()).get_document(
            user.organization_id, document.company_id, document.raw_key,
            document.sha256, MAX_XML_BYTES)
    except Exception:
        raise HTTPException(503, "XML original indisponível ou sem integridade") from None
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
        action="document.raw.downloaded", entity_id=document.id))
    await session.commit()
    return RawResponse(data, media_type="application/xml",
        headers={"X-Content-SHA256": document.sha256, "Cache-Control": "no-store",
                 "Content-Disposition": f'attachment; filename="{document.id}.xml"'})
