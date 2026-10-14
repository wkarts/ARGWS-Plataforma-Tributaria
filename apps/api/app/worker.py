"""Celery worker and bounded polling loop for CBS asynchronous queries."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

from celery import Celery
from sqlalchemy import select

from app.config import get_settings
from app.db import Session, engine
from app.models import AuditEvent, Company, Query, Record, now
from app.normalizer import InvalidReceipt, normalize
from app.provider import ProviderError, provider_for
from app.security import webhook_token
from app.storage import BlobStore

settings = get_settings()
celery_app = Celery("cbs", broker=settings.celery_broker, include=["app.worker"])
celery_app.conf.update(task_default_queue="cbs", task_serializer="json", accept_content=["json"],
                       task_ignore_result=True, worker_prefetch_multiplier=1,
                       broker_connection_retry_on_startup=True,
                       beat_schedule={
                           "poll-due-cbs": {"task": "cbs.tick", "schedule": 30.0},
                           "schedule-opt-in-cbs": {"task": "cbs.auto_schedule", "schedule": 900.0},
                       })


def aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def due_ids(limit: int = 1) -> list[str]:
    async with Session() as session, session.begin():
        rows = (await session.scalars(select(Query).where(
            Query.status.in_(["queued", "awaiting", "processing", "opening"]),
            Query.next_poll_at <= now()).order_by(Query.next_poll_at, Query.id)
            .with_for_update(skip_locked=True).limit(limit))).all()
        ids = []
        for query in rows:
            if query.status == "opening":
                query.status = "uncertain"
                query.error = "Abertura sem tíquete persistido: verificação manual necessária"
                query.next_poll_at = None
                continue
            if query.status == "processing":
                query.status = "awaiting"  # Polling is repeatable; opening is not.
            prior = query.status
            query.status = "opening" if prior == "queued" else "processing"
            query.next_poll_at = now() + timedelta(minutes=10)  # Lease after worker loss.
            if prior == "queued":
                query.opening_started_at = now()
            ids.append(query.id)
        return ids


async def mark(query_id: str, *, status: str, error: str | None = None,
               ticket: str | None = None, delay: int | None = None) -> None:
    async with Session() as session, session.begin():
        query = await session.get(Query, query_id, with_for_update=True)
        if not query or query.status == "completed":
            return
        query.status = status
        query.error = error
        if ticket is not None:
            query.ticket = ticket
        query.next_poll_at = now() + timedelta(seconds=delay) if delay is not None else None
        if status == "awaiting":
            query.poll_count += 1


async def process_one(query_id: str) -> None:
    async with Session() as session:
        query = await session.get(Query, query_id)
        if query is None or query.status not in {"opening", "processing"}:
            return
        company = await session.scalar(select(Company).where(Company.id == query.company_id,
            Company.organization_id == query.organization_id))
        if company is None:
            await mark(query_id, status="failed", error="Empresa da consulta ausente")
            return
        resource, environment, base = query.resource, query.environment, company.cnpj_base
        ticket, org_id, company_id = query.ticket, query.organization_id, company.id
        opening_started = query.opening_started_at
        stage = query.status
    provider = provider_for(environment, settings)
    if stage == "opening":
        callback = (settings.cbs_webhook_base_url.rstrip("/") +
                    f"/api/v1/webhooks/cbs/{query_id}/{webhook_token(query_id)}")
        try:
            ticket = await provider.open(resource, base, environment, callback)
        except ProviderError as exc:
            # A timed out POST or HTTP error may still have registered remotely.
            # Without an official idempotency key, reopening is unsafe.
            if exc.uncertain:
                await mark(query_id, status="uncertain",
                           error="Abertura sem confirmação; verificar no ambiente oficial")
            elif exc.retryable:
                await mark(query_id, status="queued",
                           error="Credencial ou serviço indisponível antes da abertura", delay=300)
            else:
                await mark(query_id, status="failed", error="Abertura recusada antes do processamento")
            return
        await mark(query_id, status="awaiting", ticket=ticket, delay=0 if environment == "fixture" else 120)
        return
    if not ticket:
        await mark(query_id, status="uncertain", error="Tíquete ausente após abertura")
        return
    if opening_started and now() - aware(opening_started) > timedelta(hours=4, minutes=10):
        await mark(query_id, status="failed", error="Prazo de processamento oficial excedido")
        return
    try:
        # Even when a webhook arrives, query the authenticated endpoint. The
        # callback carries no documented signature and its signed URL is ignored.
        situation = await provider.poll(ticket, environment)
        state = situation.get("estado")
        if state in {"PENDENTE", "EM_PROCESSAMENTO"}:
            await mark(query_id, status="awaiting", delay=120)
            return
        if state == "ERRO":
            code = str(situation.get("codigoErro", ""))[:100]
            await mark(query_id, status="failed", error=f"Erro oficial: {code}")
            return
        if state != "CONCLUIDA":
            raise InvalidReceipt("Estado desconhecido")
        data = await provider.download(ticket, resource, base, situation)
        blob = BlobStore(settings)
        await blob.ensure_bucket()
        raw_key, digest = await blob.put(org_id, company_id, query_id, data)
        # Keep a durable pointer even if the new schema cannot be normalized.
        async with Session() as session, session.begin():
            locked = await session.get(Query, query_id, with_for_update=True)
            if locked and locked.status != "completed":
                locked.raw_key = raw_key
                locked.raw_sha256 = digest
        # Preserve the numeric lexeme; Decimal(str(value)) in normalizer is exact.
        # SQLAlchemy JSON projections remain serializable while S3 keeps bytes.
        payload = json.loads(data, parse_float=str)
        if not isinstance(payload, dict):
            raise InvalidReceipt("Objeto JSON inválido")
        records = normalize(resource, payload, ticket=ticket, cnpj_base=base, digest=digest)
        async with Session() as session, session.begin():
            query = await session.get(Query, query_id, with_for_update=True)
            if query is None or query.status == "completed":
                return
            for item in records:
                session.add(Record(organization_id=org_id, company_id=company_id,
                                   query_id=query_id, **item))
            query.raw_key = raw_key
            query.raw_sha256 = digest
            query.status = "completed"
            query.error = None
            query.next_poll_at = None
            session.add(AuditEvent(organization_id=org_id, actor="worker",
                action="query.completed", entity_id=query_id,
                metadata_json={"sha256": digest, "records": len(records)}))
    except ProviderError as exc:
        if exc.retryable:
            await mark(query_id, status="awaiting", error="Falha temporária na consulta oficial", delay=300)
        else:
            await mark(query_id, status="failed", error="Falha definitiva no retorno oficial")
    except (InvalidReceipt, json.JSONDecodeError, UnicodeDecodeError):
        await mark(query_id, status="failed", error="Arquivo de retorno inválido; revisão manual necessária")
    except Exception:
        # Retain the ticket and retry the safe GET; no signed URL or token is logged.
        await mark(query_id, status="awaiting", error="Persistência temporariamente indisponível", delay=300)


async def run_tick() -> int:
    ids = await due_ids()
    for query_id in ids:
        await process_one(query_id)
    return len(ids)


@celery_app.task(name="cbs.tick")
def tick() -> int:
    async def run() -> int:
        try:
            return await run_tick()
        finally:
            await engine.dispose()  # asyncpg pools must not cross asyncio.run loops
    return asyncio.run(run())


@celery_app.task(name="cbs.auto_schedule")
def auto_schedule() -> int:
    from app.auto_sync import schedule_due

    async def run() -> int:
        try:
            return await schedule_due()
        finally:
            await engine.dispose()

    return asyncio.run(run())


if __name__ == "__main__":
    celery_app.worker_main(["worker", "--loglevel=INFO", "--queues=cbs"])
