"""Company-scoped agent credentials and immutable fiscal XML snapshots.

Revision ID: 20261014_0002
Revises: 20261014_0001
"""

from alembic import op
import sqlalchemy as sa

revision = "20261014_0002"
down_revision = "20261014_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("agent_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    for col in ("organization_id", "company_id"):
        op.create_index(f"ix_agent_credentials_{col}", "agent_credentials", [col])
    op.create_table("fiscal_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agent_credentials.id"), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("raw_key", sa.String(512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("document_key", sa.String(44), nullable=True),
        sa.Column("model", sa.String(4), nullable=True),
        sa.Column("issuer_cnpj", sa.String(14), nullable=True),
        sa.Column("recipient_cnpj", sa.String(14), nullable=True),
        sa.Column("period", sa.String(7), nullable=True),
        sa.Column("issue_at", sa.String(40), nullable=True),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("cbs_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("ibs_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("signature_present", sa.Boolean(), nullable=False),
        sa.Column("authorization_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "company_id", "sha256", name="uq_fiscal_doc_company_sha256"))
    for col in ("organization_id", "company_id"):
        op.create_index(f"ix_fiscal_documents_{col}", "fiscal_documents", [col])
    op.create_index("ix_fiscal_doc_company_key", "fiscal_documents",
                    ["organization_id", "company_id", "document_key"])
    op.create_index("ix_fiscal_doc_company_period", "fiscal_documents",
                    ["organization_id", "company_id", "period"])


def downgrade() -> None:
    op.drop_table("fiscal_documents")
    op.drop_table("agent_credentials")
