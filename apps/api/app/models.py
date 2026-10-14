from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uuid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InstallationBrand(Base):
    """One visual identity for the entire customer-hosted installation."""

    __tablename__ = "installation_brand"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"))
    display_name: Mapped[str] = mapped_column(String(160))
    short_name: Mapped[str] = mapped_column(String(40))
    primary_color: Mapped[str] = mapped_column(String(7))
    secondary_color: Mapped[str] = mapped_column(String(7))
    logo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class InstallationBrandAsset(Base):
    __tablename__ = "installation_brand_assets"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    media_type: Mapped[str] = mapped_column(String(32))
    content: Mapped[bytes] = mapped_column(LargeBinary)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    username: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="admin")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)


class AuthAttempt(Base):
    """Persisted login throttle shared by API restarts in a self installation."""

    __tablename__ = "auth_attempts"
    username_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    window_end: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class OAuthTokenBudget(Base):
    """Durable local issuance budget; access tokens themselves stay in memory."""

    __tablename__ = "oauth_token_budgets"
    credential_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    issued: Mapped[int] = mapped_column(Integer, nullable=False)
    window_end: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("organization_id", "cnpj", name="uq_company_org_cnpj"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    cnpj: Mapped[str] = mapped_column(String(14))
    cnpj_base: Mapped[str] = mapped_column(String(8), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Query(Base):
    __tablename__ = "queries"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_query_org_idempotency"),
        Index("ix_query_due", "status", "next_poll_at"),
        Index("uq_query_active_base", "organization_id", "environment", "cnpj_base",
              unique=True, postgresql_where=text("status IN ('queued','opening','awaiting','processing','uncertain')"),
              sqlite_where=text("status IN ('queued','opening','awaiting','processing','uncertain')")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    cnpj_base: Mapped[str] = mapped_column(String(8), index=True)
    resource: Mapped[str] = mapped_column(String(20))
    environment: Mapped[str] = mapped_column(String(20))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    ticket: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    webhook_secret_hash: Mapped[str] = mapped_column(String(64))
    raw_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    raw_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    poll_count: Mapped[int] = mapped_column(Integer, default=0)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    opening_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (UniqueConstraint("query_id", "resource", "source_key", "period", name="uq_record_source_snapshot"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    query_id: Mapped[str] = mapped_column(ForeignKey("queries.id"), index=True)
    resource: Mapped[str] = mapped_column(String(20))
    period: Mapped[str] = mapped_column(String(12))
    source_key: Mapped[str] = mapped_column(String(160))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    principal: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    penalty: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    interest: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    credit_to_appropriate: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    credit_appropriated: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    credit_used: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    credit_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    provenance: Mapped[dict] = mapped_column(JSON)
    raw: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StockCreditClaim(Base):
    __tablename__ = "stock_credit_claims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    inventory_date: Mapped[str] = mapped_column(String(10))
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    appropriated_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    used_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    evidence_ref: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CommercialInstallment(Base):
    __tablename__ = "commercial_installments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    document_ref: Mapped[str] = mapped_column(String(255))
    installment_no: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentCredential(Base):
    """Company-scoped upload secret. Only its digest is persisted."""

    __tablename__ = "agent_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    label: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FiscalDocument(Base):
    """An immutable, content-addressed XML snapshot and its untrusted XML claims."""

    __tablename__ = "fiscal_documents"
    __table_args__ = (
        UniqueConstraint("organization_id", "company_id", "sha256", name="uq_fiscal_doc_company_sha256"),
        Index("ix_fiscal_doc_company_key", "organization_id", "company_id", "document_key"),
        Index("ix_fiscal_doc_company_period", "organization_id", "company_id", "period"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agent_credentials.id"), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    raw_key: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    document_key: Mapped[str | None] = mapped_column(String(44), nullable=True)
    model: Mapped[str | None] = mapped_column(String(4), nullable=True)
    issuer_cnpj: Mapped[str | None] = mapped_column(String(14), nullable=True)
    recipient_cnpj: Mapped[str | None] = mapped_column(String(14), nullable=True)
    period: Mapped[str | None] = mapped_column(String(7), nullable=True)
    issue_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    direction: Mapped[str] = mapped_column(String(12), default="unknown")
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    cbs_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    ibs_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    signature_present: Mapped[bool] = mapped_column(Boolean)
    authorization_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
