"""Record immutable, versioned local tax calculations by company.

Revision ID: 20261027_0006
Revises: 20261014_0005
"""
from alembic import op
import sqlalchemy as sa

revision = "20261027_0006"
down_revision = "20261014_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("tax_calculations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("regime", sa.String(20), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("response_sha256", sa.String(64), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("engine_version_json", sa.JSON(), nullable=False),
        sa.Column("calculated_totals_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_tax_calculations_organization_id", "tax_calculations", ["organization_id"])
    op.create_index("ix_tax_calculations_company_id", "tax_calculations", ["company_id"])
    op.create_index("ix_tax_calc_company_created", "tax_calculations",
                    ["organization_id", "company_id", "created_at"])
    op.create_unique_constraint("uq_tax_calculation_idempotency", "tax_calculations",
                                ["organization_id", "company_id", "idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_tax_calculation_idempotency", "tax_calculations", type_="unique")
    op.drop_index("ix_tax_calc_company_created", table_name="tax_calculations")
    op.drop_index("ix_tax_calculations_company_id", table_name="tax_calculations")
    op.drop_index("ix_tax_calculations_organization_id", table_name="tax_calculations")
    op.drop_table("tax_calculations")
