import asyncio
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import auto_sync
from app.config import Settings, get_settings
from app.models import Base, Company, Organization, Query, now


def test_auto_sync_is_opt_in_and_queues_only_one_query_per_base(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fixture-secret-longer-than-32-characters")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auto_sync, "Session", session_factory)

    async def exercise():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session, session.begin():
            org = Organization(name="Simulada")
            session.add(org)
            await session.flush()
            session.add_all([
                Company(organization_id=org.id, name="Matriz", cnpj="12345678000195",
                        cnpj_base="12345678"),
                Company(organization_id=org.id, name="Filial", cnpj="12345678000276",
                        cnpj_base="12345678"),
                Company(organization_id=org.id, name="Não autorizada", cnpj="87654321000198",
                        cnpj_base="87654321"),
            ])
        settings = Settings(secret_key="fixture-secret-longer-than-32-characters",
                            provider_mode="receita", cbs_restricted_enabled=True,
                            cbs_auto_sync_enabled=True,
                            cbs_allowed_bases="12345678", cbs_credential_organization_id=org.id,
                            cbs_token_url="https://api.receitafederal.gov.br/token",
                            cbs_client_id="synthetic", cbs_client_secret="synthetic",
                            cbs_webhook_base_url="https://example.test")
        assert await auto_sync.schedule_due(Settings(cbs_auto_sync_enabled=False)) == 0
        assert await auto_sync.schedule_due(settings) == 1
        assert await auto_sync.schedule_due(settings) == 0
        async with session_factory() as session, session.begin():
            queries = (await session.scalars(select(Query))).all()
            assert len(queries) == 1
            assert queries[0].resource == "creditos" or queries[0].resource == "debitos"
            assert queries[0].status == "queued"
            assert len(queries[0].webhook_secret_hash) == 64
            queries[0].status = "completed"
            queries[0].opening_started_at = now() - timedelta(days=2)
        assert await auto_sync.schedule_due(settings) == 1
        async with session_factory() as session:
            queries = (await session.scalars(select(Query))).all()
            assert len(queries) == 2
            assert {q.resource for q in queries} == {"debitos", "creditos"}
            assert {q.cnpj_base for q in queries} == {"12345678"}
        await engine.dispose()

    try:
        asyncio.run(exercise())
    finally:
        get_settings.cache_clear()


def test_auto_sync_does_not_reopen_failed_resource_on_later_day(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fixture-secret-longer-than-32-characters")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auto_sync, "Session", session_factory)

    async def exercise():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session, session.begin():
            org = Organization(name="Simulada")
            session.add(org)
            await session.flush()
            session.add(Company(organization_id=org.id, name="Matriz",
                                cnpj="12345678000195", cnpj_base="12345678"))
        settings = Settings(secret_key="fixture-secret-longer-than-32-characters",
                            provider_mode="receita", cbs_restricted_enabled=True,
                            cbs_auto_sync_enabled=True, cbs_auto_sync_resources="debitos",
                            cbs_allowed_bases="12345678", cbs_credential_organization_id=org.id,
                            cbs_token_url="https://api.receitafederal.gov.br/token",
                            cbs_client_id="synthetic", cbs_client_secret="synthetic",
                            cbs_webhook_base_url="https://example.test")
        assert await auto_sync.schedule_due(settings) == 1
        async with session_factory() as session, session.begin():
            query = await session.scalar(select(Query))
            query.status = "failed"
        original_now = auto_sync.now
        monkeypatch.setattr(auto_sync, "now", lambda: original_now() + timedelta(days=1))
        assert await auto_sync.schedule_due(settings) == 0
        async with session_factory() as session:
            assert len((await session.scalars(select(Query))).all()) == 1
        await engine.dispose()

    try:
        asyncio.run(exercise())
    finally:
        get_settings.cache_clear()
