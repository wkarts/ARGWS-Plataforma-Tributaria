"""Company-scoped persistence and retrieval of real local-engine tax calculations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query as QueryParam, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AuditEvent, Company, TaxCalculation, User
from app.security import current_user
from app.tax_calculator import TaxCalculationIn, canonical, digest, execute_calculator

router = APIRouter(prefix="/api/v1", tags=["tax-calculator"])


def _out(value: TaxCalculation, include_result: bool) -> dict:
    result = {
        "id": value.id,
        "company_id": value.company_id,
        "regime": value.regime,
        "created_at": value.created_at,
        "request_sha256": value.request_sha256,
        "response_sha256": value.response_sha256,
        "engine_version": value.engine_version_json,
        "totals": value.calculated_totals_json,
        "calculation_source": "local_normative_engine",
        "government_assessment": False,
        "fiscal_credit_appropriated": False,
    }
    if include_result:
        result["request"] = value.request_json
        result["engine_response"] = value.response_json
    return result


async def _company(session: AsyncSession, user: User, company_id: str) -> Company:
    company = await session.scalar(select(Company).where(
        Company.id == company_id, Company.organization_id == user.organization_id))
    if company is None:
        raise HTTPException(404, "Empresa não encontrada")
    return company


@router.post("/tax-calculations", status_code=201)
async def calculate(
    data: TaxCalculationIn,
    response: Response,
    idempotency_key: str | None = Header(default=None, min_length=8, max_length=128),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    company = await _company(session, user, str(data.company_id))
    request_data = {"regime": data.regime, "payload": data.payload}
    request_hash = digest(canonical(request_data))
    if idempotency_key:
        previous = await session.scalar(select(TaxCalculation).where(
            TaxCalculation.organization_id == user.organization_id,
            TaxCalculation.company_id == company.id,
            TaxCalculation.idempotency_key == idempotency_key))
        if previous:
            if previous.request_sha256 != request_hash:
                raise HTTPException(409, "Chave de idempotência usada para cálculo diferente")
            response.headers["Cache-Control"] = "no-store"
            response.status_code = 200
            return _out(previous, True)

    values, version, totals = await execute_calculator(data.regime, data.payload)
    calculation = TaxCalculation(
        organization_id=user.organization_id,
        company_id=company.id,
        user_id=user.id,
        regime=data.regime,
        idempotency_key=idempotency_key,
        request_sha256=request_hash,
        response_sha256=digest(canonical(values)),
        request_json=data.payload,
        response_json=values,
        engine_version_json=version,
        calculated_totals_json=totals,
    )
    session.add(calculation)
    try:
        await session.flush()
        session.add(AuditEvent(
            organization_id=user.organization_id,
            actor=user.id,
            action="tax_calculation.created",
            entity_id=calculation.id,
            metadata_json={
                "company_id": company.id,
                "regime": data.regime,
                "request_sha256": calculation.request_sha256,
                "response_sha256": calculation.response_sha256,
                "engine_version": version.get("versaoApp"),
                "rules_version": version.get("versaoDb"),
            },
        ))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if idempotency_key:
            previous = await session.scalar(select(TaxCalculation).where(
                TaxCalculation.organization_id == user.organization_id,
                TaxCalculation.company_id == company.id,
                TaxCalculation.idempotency_key == idempotency_key))
            if previous and previous.request_sha256 == request_hash:
                response.headers["Cache-Control"] = "no-store"
                response.status_code = 200
                return _out(previous, True)
        raise HTTPException(409, "Não foi possível persistir cálculo duplicado") from None
    response.headers["Cache-Control"] = "no-store"
    return _out(calculation, True)


@router.get("/tax-calculations")
async def list_calculations(
    company_id: str,
    response: Response,
    cursor: str | None = None,
    limit: int = QueryParam(40, ge=1, le=100),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _company(session, user, company_id)
    if cursor is not None and (not cursor.isdecimal() or int(cursor) > 1_000_000):
        raise HTTPException(422, "Cursor inválido")
    offset = int(cursor or 0)
    values = (await session.scalars(select(TaxCalculation).where(
        TaxCalculation.organization_id == user.organization_id,
        TaxCalculation.company_id == company_id,
    ).order_by(TaxCalculation.created_at.desc(), TaxCalculation.id.desc())
        .offset(offset).limit(limit + 1))).all()
    response.headers["Cache-Control"] = "no-store"
    return {"items": [_out(row, False) for row in values[:limit]],
            "next_cursor": str(offset + limit) if len(values) > limit else None}


@router.get("/tax-calculations/{calculation_id}")
async def get_calculation(
    calculation_id: str,
    response: Response,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    value = await session.scalar(select(TaxCalculation).where(
        TaxCalculation.id == calculation_id,
        TaxCalculation.organization_id == user.organization_id))
    if value is None:
        raise HTTPException(404, "Cálculo não encontrado")
    response.headers["Cache-Control"] = "no-store"
    return _out(value, True)
