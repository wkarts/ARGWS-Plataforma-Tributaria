"""Multi-user roles, group/company grants, profile MFA and one-time emails.

Revision ID: 20261027_0007
Revises: 20261027_0006
Existing users retain all company access; newly invited users are restricted.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261027_0007"
down_revision = "20261027_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, typ, default in (
        ("full_name", sa.String(160), ""),
        ("position", sa.String(120), ""),
        ("company_access_mode", sa.String(16), "all"),
    ):
        op.add_column("users", sa.Column(name, typ, nullable=False, server_default=default))
    op.add_column("users", sa.Column("permission_keys", sa.JSON(), nullable=False,
                                     server_default="[]"))
    op.add_column("users", sa.Column("totp_secret", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("totp_pending", sa.String(512), nullable=True))
    op.add_column("users", sa.Column("totp_last_counter", sa.BigInteger(), nullable=False,
                                     server_default="0"))
    op.add_column("users", sa.Column("totp_recovery_hashes", sa.JSON(), nullable=False,
                                     server_default="[]"))
    op.add_column("users", sa.Column("avatar", sa.LargeBinary(), nullable=True))
    op.add_column("users", sa.Column("avatar_updated_at", sa.DateTime(timezone=True),
                                     nullable=True))
    op.create_table("access_groups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"),
                  nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("permission_keys", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_access_group_org_name"))
    op.create_index("ix_access_groups_organization_id", "access_groups", ["organization_id"])
    op.create_table("group_memberships",
        sa.Column("group_id", sa.String(36), sa.ForeignKey("access_groups.id"),
                  primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True))
    op.create_table("user_company_grants",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), primary_key=True))
    op.create_table("group_company_grants",
        sa.Column("group_id", sa.String(36), sa.ForeignKey("access_groups.id"),
                  primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"),
                  primary_key=True))
    op.create_table("account_actions",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"),
                  nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_account_actions_organization_id", "account_actions", ["organization_id"])
    op.create_index("ix_account_actions_user_id", "account_actions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_account_actions_user_id", table_name="account_actions")
    op.drop_index("ix_account_actions_organization_id", table_name="account_actions")
    op.drop_table("account_actions")
    for table in ("group_company_grants", "user_company_grants", "group_memberships"):
        op.drop_table(table)
    op.drop_index("ix_access_groups_organization_id", table_name="access_groups")
    op.drop_table("access_groups")
    for col in ("avatar_updated_at", "avatar", "totp_recovery_hashes", "totp_last_counter",
                "totp_pending", "totp_secret", "permission_keys", "company_access_mode",
                "position", "full_name"):
        op.drop_column("users", col)
