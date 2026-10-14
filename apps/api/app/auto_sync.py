"""Conservative CBS collection scheduler.

Creates at most one pending query per CNPJ base and environment. The existing
worker owns the external POST and ticket lifecycle; this scheduler never
reopens an uncertain request. It is opt-in because an authenticated official
environment and a publicly reachable callback are required.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

from sqlalchemy import func, select

from app.config import Settings, get_settings
from app.db import Session
from app.models import AuditEvent, Company, Query, now
from app.security import webhook_token_hash


ACTIVE = ("queued", "opening", "awaiting", "processing", "uncertain")


def utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def schedule_due(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if not settings.cbs_auto_sync_enabled:
        return 0
    settings.validate_security()
    current = now()
    queued = 0
    async with Session() as session, session.begin():
        companies = (await session.scalars(select(Company).where(
            Company.organization_id == settings.cbs_credential_organization_id,
            Company.cnpj_base.in_(settings.allowed_bases),
        ).order_by(Company.cnpj_base, Company.id).with_for_update(skip_locked=True))).all()
        seen_bases: set[str] = set()
        for company in companies:
            base = company.cnpj_base
            if base in seen_bases:
                continue
            seen_bases.add(base)
            scope = (Query.organization_id == company.organization_id,
                     Query.cnpj_base == base,
                     Query.environment == settings.cbs_auto_sync_environment)
            active = await session.scalar(select(Query.id).where(*scope,
                Query.status.in_(ACTIVE)).limit(1))
            if active:
                continue
            openings = await session.scalar(select(func.count()).select_from(Query).where(
                *scope, Query.opening_started_at >= current - timedelta(hours=24)))
            if openings >= 4:
                continue
            candidates: list[tuple[datetime, str]] = []
            for resource in settings.auto_sync_resources:
                last = await session.scalar(select(Query).where(*scope,
                    Query.resource == resource)
                    .order_by(Query.created_at.desc(), Query.id.desc()).limit(1))
                # A terminal failure can follow a remote opening. Require a
                # deliberate manual query before the scheduler resumes this
                # resource, even on a later calendar day.
                if last is not None and last.status == "failed":
                    continue
                last_time = utc(last.opening_started_at or last.created_at) if last else None
                if last_time is None or current - last_time >= timedelta(hours=24):
                    candidates.append((last_time or datetime.min.replace(tzinfo=timezone.utc),
                                       resource))
            for _, resource in sorted(candidates):
                identity = (f"auto:{settings.cbs_auto_sync_environment}:{base}:"
                            f"{resource}:{current.date().isoformat()}")
                key = sha256(identity.encode()).hexdigest()
                # A failed/uncertain opening must be reviewed, never retried
                # with a new ticket just because a scheduler runs again.
                prior = await session.scalar(select(Query.id).where(
                    Query.organization_id == company.organization_id,
                    Query.idempotency_key == key).limit(1))
                if prior:
                    continue
                query = Query(organization_id=company.organization_id,
                    company_id=company.id, cnpj_base=base, resource=resource,
                    environment=settings.cbs_auto_sync_environment,
                    idempotency_key=key,
                    request_fingerprint=sha256(identity.encode()).hexdigest(),
                    status="queued", webhook_secret_hash="pending",
                    next_poll_at=current)
                session.add(query)
                await session.flush()
                query.webhook_secret_hash = webhook_token_hash(query.id)
                session.add(AuditEvent(organization_id=company.organization_id,
                    actor="scheduler", action="query.auto_scheduled", entity_id=query.id,
                    metadata_json={"resource": resource,
                                   "environment": settings.cbs_auto_sync_environment}))
                queued += 1
                break  # One active request per base; the next tick may queue another.
    return queued
