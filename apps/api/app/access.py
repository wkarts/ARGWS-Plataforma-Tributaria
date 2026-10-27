"""Tenant-scoped resource permissions and inherited group/company access."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (AccessGroup, Company, GroupCompanyGrant, GroupMembership,
                        User, UserCompanyGrant)

PERMISSION_KEYS = frozenset({
    "companies.read", "companies.manage", "queries.read", "queries.create",
    "documents.read", "documents.ingest", "reports.read", "finance.read",
    "finance.manage", "calculator.read", "calculator.create",
    "agents.manage", "users.read", "users.manage", "groups.manage",
    "branding.manage",
})
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"companies.read", "queries.read", "documents.read",
                         "reports.read", "finance.read", "calculator.read"}),
    "operator": frozenset({"companies.read", "queries.read", "queries.create",
                           "documents.read", "documents.ingest", "reports.read",
                           "finance.read", "finance.manage", "calculator.read",
                           "calculator.create"}),
    "manager": frozenset(PERMISSION_KEYS - {"users.manage", "groups.manage",
                                            "branding.manage"}),
    "admin": PERMISSION_KEYS,
    "reader": frozenset({"companies.read", "queries.read", "documents.read",
                          "reports.read", "finance.read", "calculator.read"}),
}


async def permissions(user: User, session: AsyncSession) -> set[str]:
    if user.role == "admin":
        return set(PERMISSION_KEYS)
    allowed = set(ROLE_PERMISSIONS.get(user.role, ROLE_PERMISSIONS["viewer"]))
    allowed.update(user.permission_keys or [])
    rows = (await session.scalars(
        select(AccessGroup.permission_keys)
        .join(GroupMembership, AccessGroup.id == GroupMembership.group_id)
        .where(GroupMembership.user_id == user.id,
               AccessGroup.organization_id == user.organization_id)
    )).all()
    for group_keys in rows:
        allowed.update(group_keys or [])
    return allowed & PERMISSION_KEYS


async def require_permission(user: User, session: AsyncSession, key: str) -> None:
    if key not in await permissions(user, session):
        raise HTTPException(403, "Recurso não autorizado para o perfil ou grupo")


async def accessible_companies(user: User, session: AsyncSession) -> set[str] | None:
    # None = all companies in this organization. Existing accounts keep access.
    if user.role == "admin" or user.company_access_mode == "all":
        return None
    direct = (await session.scalars(
        select(UserCompanyGrant.company_id)
        .join(Company, Company.id == UserCompanyGrant.company_id)
        .where(UserCompanyGrant.user_id == user.id,
               Company.organization_id == user.organization_id)
    )).all()
    grouped = (await session.scalars(
        select(GroupCompanyGrant.company_id)
        .join(GroupMembership, GroupMembership.group_id == GroupCompanyGrant.group_id)
        .join(Company, Company.id == GroupCompanyGrant.company_id)
        .join(AccessGroup, AccessGroup.id == GroupMembership.group_id)
        .where(GroupMembership.user_id == user.id,
               Company.organization_id == user.organization_id,
               AccessGroup.organization_id == user.organization_id)
    )).all()
    return set(direct) | set(grouped)


async def require_company(user: User, session: AsyncSession, company_id: str,
                          permission: str = "companies.read") -> None:
    await require_permission(user, session, permission)
    access = await accessible_companies(user, session)
    if access is not None and company_id not in access:
        # Deliberately indistinguishable from a company in another organization.
        raise HTTPException(404, "Empresa não encontrada")


async def limited_company_ids(user: User, session: AsyncSession, key: str) -> set[str] | None:
    await require_permission(user, session, key)
    return await accessible_companies(user, session)
