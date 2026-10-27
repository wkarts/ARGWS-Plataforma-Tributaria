"""Add company registry metadata and one-time account tokens.

Revision ID: 20261027_0007
Revises: 20261027_0006
"""
from alembic import op
import sqlalchemy as sa

revision = "20261027_0007"
down_revision = "20261027_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column, type_ in (
        ("trade_name", sa.String(180)), ("registry_status", sa.String(100)),
        ("state", sa.String(2)), ("city", sa.String(160)),
        ("address", sa.String(255)), ("registry_source", sa.String(40)),
        ("registry_checked_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("companies", sa.Column(column, type_, nullable=True))
    op.create_table(
        "account_actions",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_account_actions_user_id", "account_actions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_account_actions_user_id", table_name="account_actions")
    op.drop_table("account_actions")
    for column in ("registry_checked_at", "registry_source", "address", "city",
                   "state", "registry_status", "trade_name"):
        op.drop_column("companies", column)
