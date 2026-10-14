"""Single installation identity and normalized local branding asset.

Revision ID: 20261014_0003
Revises: 20261014_0002
"""

from alembic import op
import sqlalchemy as sa

revision = "20261014_0003"
down_revision = "20261014_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "installation_brand",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("short_name", sa.String(40), nullable=False),
        sa.Column("primary_color", sa.String(7), nullable=False),
        sa.Column("secondary_color", sa.String(7), nullable=False),
        sa.Column("logo_sha256", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "installation_brand_assets",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("installation_brand_assets")
    op.drop_table("installation_brand")
