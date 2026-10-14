"""Read-only, period-scoped comparison of XML evidence and CBS observations."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Company, FiscalDocument, Query, Record, User
from app.reconciliation import assisted_preview, reconcile
from app.security import current_user


router = APIRouter(prefix="/api/v1", tags=["reconciliation"])
MAX_ROWS = 5000


@router.get("/assisted-preview")
async def preview(company_id: str, period: str,
                  user: User = Depends(current_user),
                  session: AsyncSession = Depends(get_session)) -> dict:
    """Read-only pre-assessment from local XML claims and ingested CBS receipts."""
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", period):
        raise HTTPException(422, "Competência inválida; use AAAA-MM")
    company = await session.scalar(select(Company).where(
        Company.id == company_id, Company.organization_id == user.organization_id))
    if company is None:
        raise HTTPException(404, "Empresa não encontrada")
    base_count = await session.scalar(select(func.count()).select_from(Company).where(
        Company.organization_id == user.organization_id,
        Company.cnpj_base == company.cnpj_base))
    year, month = period.split("-")
    variants = (period, year + month, f"{month}/{year}")
    # A fixture receipt is intentionally excluded: it cannot be described as
    # an observation from Receita. All three identities are constrained here.
    rows = (await session.execute(select(Record, Query).join(Query, Query.id == Record.query_id).where(
        Record.organization_id == user.organization_id,
        Record.company_id == company_id,
        Record.period.in_(variants),
        Query.organization_id == user.organization_id,
        Query.company_id == company_id,
        Query.status == "completed",
        Query.environment.in_(("restricted", "production")),
        Query.raw_sha256.is_not(None),
        Record.resource.in_(("debitos", "creditos")),
    ).limit(MAX_ROWS + 1))).all()
    docs = (await session.scalars(select(FiscalDocument).where(
        FiscalDocument.organization_id == user.organization_id,
        FiscalDocument.company_id == company_id,
        FiscalDocument.period.in_(variants)).limit(MAX_ROWS + 1))).all()
    if len(rows) > MAX_ROWS or len(docs) > MAX_ROWS:
        raise HTTPException(413, "Competência excede o limite da prévia interativa")
    unperioded = await session.scalar(select(func.count()).select_from(FiscalDocument).where(
        FiscalDocument.organization_id == user.organization_id,
        FiscalDocument.company_id == company_id,
        FiscalDocument.period.is_(None)))
    receipt_ids = {query.id for _, query in rows}
    result = assisted_preview((record for record, _ in rows), docs,
                              organization_id=user.organization_id, company_id=company_id,
                              period=period, official_receipts=len(receipt_ids),
                              company_scope_complete=base_count == 1,
                              unperioded_document_count=unperioded or 0)
    return {"company_id": company_id, "period": period, **result}


@router.get("/reconciliation")
async def comparison(company_id: str, period: str,
                     user: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)) -> dict:
    if not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", period):
        raise HTTPException(422, "Competência inválida; use AAAA-MM")
    company = await session.scalar(select(Company).where(
        Company.id == company_id, Company.organization_id == user.organization_id))
    if company is None:
        raise HTTPException(404, "Empresa não encontrada")
    companies_at_base = await session.scalar(select(func.count()).select_from(Company).where(
        Company.organization_id == user.organization_id,
        Company.cnpj_base == company.cnpj_base))
    year, month = period.split("-")
    variants = (period, year + month, f"{month}/{year}")
    records = (await session.scalars(select(Record).where(
        Record.organization_id == user.organization_id,
        Record.company_id == company_id,
        Record.period.in_(variants)).limit(MAX_ROWS + 1))).all()
    documents = (await session.scalars(select(FiscalDocument).where(
        FiscalDocument.organization_id == user.organization_id,
        FiscalDocument.company_id == company_id,
        FiscalDocument.period.in_(variants)).limit(MAX_ROWS + 1))).all()
    if len(records) > MAX_ROWS or len(documents) > MAX_ROWS:
        raise HTTPException(413, "Competência excede o limite de comparação interativa")
    unperioded_documents = await session.scalar(select(func.count()).select_from(FiscalDocument).where(
        FiscalDocument.organization_id == user.organization_id,
        FiscalDocument.company_id == company_id,
        FiscalDocument.period.is_(None)))
    report = reconcile(records, documents, organization_id=user.organization_id,
                       company_id=company_id, period=period,
                       company_scope_complete=companies_at_base == 1)
    return {"company_id": company_id, "period": period, **report,
            "basis": "comparação de valores informados; não constitui apuração fiscal",
            "official_coverage_proven": False,
            "company_scope_complete": companies_at_base == 1,
            "unperioded_document_count": unperioded_documents or 0}
