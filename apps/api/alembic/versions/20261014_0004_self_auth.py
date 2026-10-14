"""Persist login throttling for installations without Redis.

Revision ID: 20261014_0004
Revises: 20261014_0003
"""

from alembic import op
import sqlalchemy as sa

revision = "20261014_0004"
down_revision = "20261014_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("auth_attempts",
        sa.Column("username_hash", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("window_end", sa.BigInteger(), nullable=False))
    op.create_index("ix_auth_attempts_window_end", "auth_attempts", ["window_end"])


def downgrade() -> None:
    op.drop_index("ix_auth_attempts_window_end", table_name="auth_attempts")
    op.drop_table("auth_attempts")
