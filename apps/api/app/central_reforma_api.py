"""Operational dashboard from persisted evidence, scoped to one company.

History and counts do not imply completion of an official assessment.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Company, FiscalDocument, Query, Record, User
from app.security import current_user

router = APIRouter(prefix="/api/v1", tags=["central-reforma"])

ACTIVE = ("queued", "opening", "awaiting", "processing")
ATTENTION = ("uncertain", "failed")


async def owned_company(session: AsyncSession, user: User, company_id: str) -> Company:
    company = await session.scalar(select(Company).where(
        Company.id == company_id, Company.organization_id == user.organization_id
    ))
    if company is None:
        raise HTTPException(404, "Empresa não encontrada")
    return company


def query_summary(value: Query) -> dict:
    return {
        "id": value.id,
        "resource": value.resource,
        "environment": value.environment,
        "status": value.status,
        "ticket": value.ticket,
        "received_sha256": value.raw_sha256,
        "has_raw_evidence": bool(value.raw_sha256),
        "has_error": bool(value.error),
        "poll_count": value.poll_count,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "official_receipt": bool(
            value.status == "completed"
            and value.environment in ("restricted", "production")
            and value.raw_sha256
        ),
    }


@router.get("/central/summary")
async def central_summary(
    company_id: str,
    response: Response,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    company = await owned_company(session, user, company_id)
    scope = (Query.organization_id == user.organization_id, Query.company_id == company.id)
    counts = (await session.execute(
        select(Query.status, func.count(Query.id)).where(*scope).group_by(Query.status)
    )).all()
    statuses = {status: count for status, count in counts}
    documents = await session.scalar(select(func.count(FiscalDocument.id)).where(
        FiscalDocument.organization_id == user.organization_id,
        FiscalDocument.company_id == company.id,
    ))
    records = await session.scalar(select(func.count(Record.id)).where(
        Record.organization_id == user.organization_id,
        Record.company_id == company.id,
    ))
    snapshots = await session.scalar(select(func.count(Query.id)).where(
        *scope,
        Query.status == "completed",
        Query.environment.in_(("restricted", "production")),
        Query.raw_sha256.is_not(None),
    ))
    recent = (await session.scalars(select(Query).where(*scope).order_by(
        Query.created_at.desc(), Query.id.desc()).limit(12))).all()
    response.headers["Cache-Control"] = "no-store"
    return {
        "company_id": company.id,
        "cnpj_base": company.cnpj_base,
        "counts": {
            "queries": sum(statuses.values()),
            "active": sum(statuses.get(key, 0) for key in ACTIVE),
            "attention": sum(statuses.get(key, 0) for key in ATTENTION),
            "completed": statuses.get("completed", 0),
            "by_status": statuses,
            "documents": documents or 0,
            "records": records or 0,
            "official_snapshots": snapshots or 0,
        },
        "recent_queries": [query_summary(row) for row in recent],
        "official_coverage_proven": False,
        "basis": "Histórico persistido por empresa. Consultas incrementais não comprovam "
                 "cobertura integral da competência, nem a existência de uma apuração homologada.",
    }


@router.get("/central/history")
async def central_history(
    company_id: str,
    response: Response,
    cursor: str | None = None,
    limit: int = QueryParam(40, ge=1, le=200),
    environment: str | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await owned_company(session, user, company_id)
    if cursor is not None and (not cursor.isdecimal() or int(cursor) > 1_000_000):
        raise HTTPException(422, "Cursor inválido")
    if environment is not None and environment not in ("fixture", "restricted", "production"):
        raise HTTPException(422, "Ambiente inválido")
    offset = int(cursor or 0)
    stmt = select(Query).where(
        Query.organization_id == user.organization_id, Query.company_id == company_id
    )
    if environment:
        stmt = stmt.where(Query.environment == environment)
    values = (await session.scalars(stmt.order_by(
        Query.created_at.desc(), Query.id.desc()).offset(offset).limit(limit + 1))).all()
    response.headers["Cache-Control"] = "no-store"
    return {
        "company_id": company_id,
        "items": [query_summary(value) for value in values[:limit]],
        "next_cursor": str(offset + limit) if len(values) > limit else None,
    }


