"""Persist the self-hosted OAuth issuance budget across worker restarts.

Revision ID: 20261014_0005
Revises: 20261014_0004
"""

from alembic import op
import sqlalchemy as sa

revision = "20261014_0005"
down_revision = "20261014_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("oauth_token_budgets",
        sa.Column("credential_hash", sa.String(64), primary_key=True),
        sa.Column("issued", sa.Integer(), nullable=False),
        sa.Column("window_end", sa.BigInteger(), nullable=False))


def downgrade() -> None:
    op.drop_table("oauth_token_budgets")
