"""Manual evidence registers kept outside the official CBS credit projection.

Neither endpoint computes presumed credits, extinguishes tax, or changes an
official credit. A commercial payment is an accounts-payable observation only.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AuditEvent, CommercialInstallment, Company, StockCreditClaim, User
from app.schemas import valid_cnpj
from app.security import admin_only, current_user


router = APIRouter(prefix="/api/v1", tags=["finance-controls"])
_MONEY = re.compile(r"\d{1,16}(?:\.\d{1,2})?\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")


def _positive_money(value: object) -> Decimal:
    # JSON floats must not silently round an amount used in fiscal evidence.
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("Valor monetário deve ser uma string decimal exata")
    raw = str(value)
    if not _MONEY.fullmatch(raw):
        raise ValueError("Use valor positivo com até 16 dígitos inteiros e duas casas decimais")
    amount = Decimal(raw)
    if amount <= 0:
        raise ValueError("Valor deve ser maior que zero")
    return amount


def _reference(value: str) -> str:
    result = value.strip()
    if not result or len(result) > 255 or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError("Referência obrigatória com até 255 caracteres, sem controles")
    return result


class StockCreditClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(min_length=36, max_length=36)
    inventory_date: date
    expected_amount: Decimal
    evidence_ref: str = Field(min_length=1, max_length=255)

    @field_validator("inventory_date", mode="before")
    @classmethod
    def valid_inventory_date(cls, value: object) -> date:
        if not isinstance(value, str) or not _DATE.fullmatch(value):
            raise ValueError("Data de inventário deve estar em AAAA-MM-DD")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError("Data de inventário inválida") from None

    @field_validator("expected_amount", mode="before")
    @classmethod
    def valid_amount(cls, value: object) -> Decimal:
        return _positive_money(value)

    @field_validator("evidence_ref")
    @classmethod
    def valid_evidence(cls, value: str) -> str:
        return _reference(value)


class CommercialInstallmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(min_length=36, max_length=36)
    document_ref: str = Field(min_length=1, max_length=255)
    installment_no: int = Field(ge=1, le=10000, strict=True)
    amount: Decimal
    paid_at: datetime | None = None

    @field_validator("document_ref")
    @classmethod
    def valid_document(cls, value: str) -> str:
        result = _reference(value)
        # A 44-digit DFe key identifies its issuer at positions 7-20. This is
        # validation of the identifier, not evidence that our company bought it.
        if re.fullmatch(r"\d{44}", result) and not valid_cnpj(result[6:20]):
            raise ValueError("CNPJ do emitente na chave do documento inválido")
        return result

    @field_validator("amount", mode="before")
    @classmethod
    def valid_amount(cls, value: object) -> Decimal:
        return _positive_money(value)

    @field_validator("paid_at", mode="before")
    @classmethod
    def valid_paid_at(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str) or not _DATETIME.fullmatch(value):
            raise ValueError("Pagamento deve conter data, hora e fuso ISO 8601")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc)
        except ValueError:
            raise ValueError("Data de pagamento inválida") from None


async def _owned_company(session: AsyncSession, user: User, company_id: str) -> Company:
    company = await session.scalar(select(Company).where(
        Company.id == company_id, Company.organization_id == user.organization_id))
    if company is None:
        raise HTTPException(404, "Empresa não encontrada")
    return company


def stock_out(value: StockCreditClaim) -> dict:
    return {"id": value.id, "company_id": value.company_id,
            "inventory_date": value.inventory_date,
            "expected_amount": format(value.expected_amount, ".2f"),
            "appropriated_amount": format(value.appropriated_amount, ".2f"),
            "used_amount": format(value.used_amount, ".2f"), "evidence_ref": value.evidence_ref,
            "status": value.status, "created_at": value.created_at,
            "category": "presumed_stock_credit_candidate",
            "evidence_verified": False,
            "basis": "registro manual em rascunho; sem cálculo, apropriação ou utilização fiscal"}


def installment_out(value: CommercialInstallment) -> dict:
    return {"id": value.id, "company_id": value.company_id,
            "document_ref": value.document_ref, "installment_no": value.installment_no,
            "amount": format(value.amount, ".2f"), "paid_at": value.paid_at,
            "created_at": value.created_at, "category": "commercial_payment",
            "basis": "parcela comercial; não comprova extinção do débito tributário nem libera crédito"}


def _offset(cursor: str | None) -> int:
    if cursor is not None and (not cursor.isdecimal() or int(cursor) > 1_000_000):
        raise HTTPException(422, "Cursor inválido")
    return int(cursor or "0")


@router.post("/stock-credit-claims", status_code=201)
async def create_stock_credit_claim(body: StockCreditClaimIn, response: Response,
                                    user: User = Depends(admin_only),
                                    session: AsyncSession = Depends(get_session)) -> dict:
    company = await _owned_company(session, user, body.company_id)
    claim = StockCreditClaim(organization_id=user.organization_id, company_id=company.id,
        inventory_date=body.inventory_date.isoformat(), expected_amount=body.expected_amount,
        appropriated_amount=Decimal("0"), used_amount=Decimal("0"),
        evidence_ref=body.evidence_ref, status="draft")
    session.add(claim)
    await session.flush()
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
        action="stock_credit_claim.created", entity_id=claim.id,
        metadata_json={"company_id": company.id}))
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return stock_out(claim)


@router.get("/stock-credit-claims")
async def list_stock_credit_claims(response: Response, company_id: str | None = None,
                                   cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                                   user: User = Depends(current_user),
                                   session: AsyncSession = Depends(get_session)) -> dict:
    offset = _offset(cursor)
    stmt = select(StockCreditClaim).where(StockCreditClaim.organization_id == user.organization_id)
    if company_id is not None:
        await _owned_company(session, user, company_id)
        stmt = stmt.where(StockCreditClaim.company_id == company_id)
    values = (await session.scalars(stmt.order_by(StockCreditClaim.created_at.desc(),
        StockCreditClaim.id.desc()).offset(offset).limit(limit + 1))).all()
    response.headers["Cache-Control"] = "no-store"
    return {"items": [stock_out(value) for value in values[:limit]],
            "next_cursor": str(offset + limit) if len(values) > limit else None}


@router.post("/commercial-installments", status_code=201)
async def create_commercial_installment(body: CommercialInstallmentIn, response: Response,
                                        user: User = Depends(admin_only),
                                        session: AsyncSession = Depends(get_session)) -> dict:
    company = await _owned_company(session, user, body.company_id)
    installment = CommercialInstallment(organization_id=user.organization_id,
        company_id=company.id, document_ref=body.document_ref,
        installment_no=body.installment_no, amount=body.amount, paid_at=body.paid_at)
    session.add(installment)
    await session.flush()
    session.add(AuditEvent(organization_id=user.organization_id, actor=user.id,
        action="commercial_installment.created", entity_id=installment.id,
        metadata_json={"company_id": company.id}))
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return installment_out(installment)


@router.get("/commercial-installments")
async def list_commercial_installments(response: Response, company_id: str | None = None,
                                       cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                                       user: User = Depends(current_user),
                                       session: AsyncSession = Depends(get_session)) -> dict:
    offset = _offset(cursor)
    stmt = select(CommercialInstallment).where(
        CommercialInstallment.organization_id == user.organization_id)
    if company_id is not None:
        await _owned_company(session, user, company_id)
        stmt = stmt.where(CommercialInstallment.company_id == company_id)
    values = (await session.scalars(stmt.order_by(CommercialInstallment.created_at.desc(),
        CommercialInstallment.id.desc()).offset(offset).limit(limit + 1))).all()
    response.headers["Cache-Control"] = "no-store"
    return {"items": [installment_out(value) for value in values[:limit]],
            "next_cursor": str(offset + limit) if len(values) > limit else None}
