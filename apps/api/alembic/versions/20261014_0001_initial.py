"""Initial tenant, query, immutable receipt and separate fiscal/commercial structures.

Revision ID: 20261014_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "20261014_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("organizations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("username", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False))
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_table("companies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(180), nullable=False),
        sa.Column("cnpj", sa.String(14), nullable=False),
        sa.Column("cnpj_base", sa.String(8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "cnpj", name="uq_company_org_cnpj"))
    op.create_index("ix_companies_organization_id", "companies", ["organization_id"])
    op.create_index("ix_companies_cnpj_base", "companies", ["cnpj_base"])
    op.create_table("queries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("cnpj_base", sa.String(8), nullable=False),
        sa.Column("resource", sa.String(20), nullable=False),
        sa.Column("environment", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("ticket", sa.String(200), nullable=True),
        sa.Column("webhook_secret_hash", sa.String(64), nullable=False),
        sa.Column("raw_key", sa.String(512), nullable=True),
        sa.Column("raw_sha256", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("poll_count", sa.Integer(), nullable=False),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opening_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_query_org_idempotency"))
    op.create_index("ix_queries_organization_id", "queries", ["organization_id"])
    op.create_index("ix_queries_company_id", "queries", ["company_id"])
    op.create_index("ix_queries_cnpj_base", "queries", ["cnpj_base"])
    op.create_index("ix_queries_ticket", "queries", ["ticket"])
    op.create_index("ix_query_due", "queries", ["status", "next_poll_at"])
    op.create_index("uq_query_active_base", "queries", ["organization_id", "environment", "cnpj_base"],
        unique=True, postgresql_where=sa.text("status IN ('queued','opening','awaiting','processing','uncertain')"),
        sqlite_where=sa.text("status IN ('queued','opening','awaiting','processing','uncertain')"))
    op.create_table("records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("query_id", sa.String(36), sa.ForeignKey("queries.id"), nullable=False),
        sa.Column("resource", sa.String(20), nullable=False),
        sa.Column("period", sa.String(12), nullable=False),
        sa.Column("source_key", sa.String(160), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("principal", sa.Numeric(18, 2), nullable=True),
        sa.Column("penalty", sa.Numeric(18, 2), nullable=True),
        sa.Column("interest", sa.Numeric(18, 2), nullable=True),
        sa.Column("credit_to_appropriate", sa.Numeric(18, 2), nullable=True),
        sa.Column("credit_appropriated", sa.Numeric(18, 2), nullable=True),
        sa.Column("credit_used", sa.Numeric(18, 2), nullable=True),
        sa.Column("credit_category", sa.String(30), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("query_id", "resource", "source_key", "period", name="uq_record_source_snapshot"))
    for col in ("organization_id", "company_id", "query_id"):
        op.create_index(f"ix_records_{col}", "records", [col])
    op.create_table("stock_credit_claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("inventory_date", sa.String(10), nullable=False),
        sa.Column("expected_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("appropriated_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("used_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("evidence_ref", sa.String(255), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for col in ("organization_id", "company_id"):
        op.create_index(f"ix_stock_credit_claims_{col}", "stock_credit_claims", [col])
    op.create_table("commercial_installments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("document_ref", sa.String(255), nullable=False),
        sa.Column("installment_no", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for col in ("organization_id", "company_id"):
        op.create_index(f"ix_commercial_installments_{col}", "commercial_installments", [col])
    op.create_table("audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(100), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_audit_events_organization_id", "audit_events", ["organization_id"])


def downgrade() -> None:
    for table in ("audit_events", "commercial_installments", "stock_credit_claims", "records", "queries", "companies", "users", "organizations"):
        op.drop_table(table)
