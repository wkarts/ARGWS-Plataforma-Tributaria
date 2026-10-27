import asyncio
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Base, Company, Organization, Query, Record, User
from app.security import hash_password, issue_token, webhook_token


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "testing-only-secret-key-longer-than-32-characters")
    monkeypatch.setenv("PROVIDER_MODE", "fixture")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool, connect_args={"check_same_thread": False})
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override

    async def seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session, session.begin():
            org1 = Organization(name="A")
            org2 = Organization(name="B")
            session.add_all([org1, org2])
            await session.flush()
            a = User(username="a@example.com", password_hash=hash_password("LongPassword-123"),
                     organization_id=org1.id, role="admin", token_version=0)
            b = User(username="b@example.com", password_hash=hash_password("LongPassword-456"),
                     organization_id=org2.id, role="admin", token_version=0)
            session.add_all([a, b])
            await session.flush()
            company = Company(organization_id=org1.id, name="Alfa", cnpj="12345678000195", cnpj_base="12345678")
            session.add(company)
            await session.flush()
            query = Query(organization_id=org1.id, company_id=company.id,
                cnpj_base="12345678", resource="creditos", environment="fixture",
                idempotency_key="existing-123", request_fingerprint="x", status="completed",
                webhook_secret_hash="h", poll_count=0)
            session.add(query)
            await session.flush()
            record = Record(organization_id=org1.id, company_id=company.id, query_id=query.id,
                resource="creditos", period="10/2026", source_key="chave", amount=10,
                credit_category="government_reported", provenance={}, raw={})
            session.add(record)
            return a, b, company, query, record

    a, b, company, query, record = asyncio.run(seed())
    try:
        with TestClient(app) as client:
            yield client, issue_token(a), issue_token(b), company, query, record, session_factory
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
        get_settings.cache_clear()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_org_cannot_read_other_company_query_record_or_raw(clients):
    client, a, b, company, query, record, _ = clients
    assert client.get("/api/v1/companies", headers=auth(b)).json()["items"] == []
    assert client.get(f"/api/v1/queries/{query.id}", headers=auth(b)).status_code == 404
    assert client.get(f"/api/v1/queries/{query.id}/raw", headers=auth(b)).status_code == 404
    assert client.get(f"/api/v1/records?query_id={query.id}", headers=auth(b)).json()["items"] == []
    assert client.get("/api/v1/records", headers=auth(a)).json()["items"][0]["id"] == record.id
    payload = {"company_id": company.id, "resource": "debitos", "environment": "fixture"}
    assert client.post("/api/v1/queries", json=payload,
        headers=auth(b) | {"Idempotency-Key": "other-org-1"}).status_code == 404
    assert client.get(f"/api/v1/coverage?company_id={company.id}", headers=auth(b)).status_code == 404


def test_idempotency_payload_conflict_and_same_key_other_org(clients):
    client, a, b, company, query, record, _ = clients
    payload = {"company_id": company.id, "resource": "debitos", "environment": "fixture"}
    headers = auth(a) | {"Idempotency-Key": "request-abc123"}
    first = client.post("/api/v1/queries", json=payload, headers=headers)
    assert first.status_code == 202, first.text
    again = client.post("/api/v1/queries", json=payload, headers=headers)
    assert again.status_code == 202 and again.json()["id"] == first.json()["id"]
    assert client.post("/api/v1/queries", json=payload | {"resource": "creditos"},
                       headers=headers).status_code == 409
    branch = client.post("/api/v1/companies",
        json={"name": "Filial", "cnpj": "12345678000276"}, headers=auth(a))
    assert branch.status_code == 201
    same_base = client.post("/api/v1/queries", json=payload | {
        "company_id": branch.json()["id"], "resource": "creditos"},
        headers=auth(a) | {"Idempotency-Key": "branch-query-1"})
    assert same_base.status_code == 409  # filial partilha quota e exclusão ativa
    other_company = client.post("/api/v1/companies", json={"name": "Beta", "cnpj": "87654321000198"},
                                headers=auth(b))
    assert other_company.status_code == 201
    other_payload = payload | {"company_id": other_company.json()["id"]}
    other = client.post("/api/v1/queries", json=other_payload,
                        headers=auth(b) | {"Idempotency-Key": "request-abc123"})
    assert other.status_code == 202 and other.json()["id"] != first.json()["id"]


def test_change_password_revokes_previous_token(clients):
    client, old_token, _, *_ = clients
    result = client.post("/api/v1/auth/change-password",
        json={"current_password": "LongPassword-123", "new_password": "New-LongPassword-123"},
        headers=auth(old_token))
    assert result.status_code == 200
    assert client.get("/api/v1/auth/me", headers=auth(old_token)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=auth(result.json()["access_token"])).status_code == 200


def test_worker_fixture_opens_polls_stores_and_projects(clients, monkeypatch):
    from app import worker
    from app.storage import BlobStore

    client, a, _, company, _, _, session_factory = clients
    monkeypatch.setattr(worker, "Session", session_factory)
    # Preserve the fixture-only worker mode used by this isolated integration test.
    monkeypatch.setattr(worker, "settings", get_settings())

    async def fake_bucket(self):
        return None

    async def fake_put(self, org, company_id, query_id, data):
        return f"test/{query_id}.json", sha256(data).hexdigest()

    monkeypatch.setattr(BlobStore, "ensure_bucket", fake_bucket)
    monkeypatch.setattr(BlobStore, "put", fake_put)
    payload = {"company_id": company.id, "resource": "creditos", "environment": "fixture"}
    opened = client.post("/api/v1/queries", json=payload,
        headers=auth(a) | {"Idempotency-Key": "worker-fixture-1"})
    assert opened.status_code == 202

    assert asyncio.run(worker.run_tick()) == 1
    awaiting = client.get(f"/api/v1/queries/{opened.json()['id']}", headers=auth(a)).json()
    assert awaiting["status"] == "awaiting"
    callback = f"/api/v1/webhooks/cbs/{awaiting['id']}/{webhook_token(awaiting['id'])}"
    assert client.head(callback).status_code == 200
    assert client.post(callback, json={"tiqueteSolicitacao": "another",
        "urlAssinada": "https://attacker.invalid/receipt"}).status_code == 409
    assert client.post(callback, json={"tiqueteSolicitacao": awaiting["ticket"],
        "urlAssinada": "https://attacker.invalid/receipt"}).status_code == 202
    assert asyncio.run(worker.run_tick()) == 1
    finished = client.get(f"/api/v1/queries/{opened.json()['id']}", headers=auth(a))
    assert finished.json()["status"] == "completed", finished.text
    assert len(finished.json()["raw_sha256"]) == 64
    records = client.get(f"/api/v1/records?query_id={opened.json()['id']}", headers=auth(a)).json()["items"]
    assert len(records) == 1
    assert records[0]["credit_to_appropriate"] == "75.10"
    assert records[0]["credit_appropriated"] == "25.00"
    assert records[0]["credit_used"] == "10.00"
    coverage = client.get(f"/api/v1/coverage?company_id={company.id}", headers=auth(a)).json()
    assert coverage["incremental_max_days"] == 8
    assert coverage["resources"]["creditos"]["last_completed_query_id"] == opened.json()["id"]

def test_incomplete_receita_keeps_companies_and_login_available_without_mock(clients, monkeypatch):
    """An invalid official integration must not disable local use or generate fixture data."""
    from app.main import capabilities
    from app import bootstrap as bootstrap_module
    from app.storage import BlobStore

    client, admin, _, company, _, _, factory = clients
    monkeypatch.setenv("PROVIDER_MODE", "receita")
    monkeypatch.setenv("CBS_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("CBS_CLIENT_ID", "")
    monkeypatch.setenv("CBS_CLIENT_SECRET", "")
    monkeypatch.setenv("CBS_TOKEN_URL", "")
    monkeypatch.setenv("CBS_WEBHOOK_BASE_URL", "")
    monkeypatch.setenv("CBS_ALLOWED_BASES", "")
    monkeypatch.setenv("CBS_CREDENTIAL_ORGANIZATION_ID", "")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "new-admin@example.test")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "VeryStrong-password-2026")
    get_settings.cache_clear()
    settings = get_settings()
    settings.validate_security(require_provider=False)
    with pytest.raises(RuntimeError, match="Credenciais, callback"):
        settings.validate_security()
    pending = settings.receipt_readiness()
    assert pending["ready"] is False
    assert "CBS_CLIENT_SECRET" in pending["missing"]
    assert "VeryStrong-password-2026" not in str(pending)

    async def fake_bucket(self):
        return None

    monkeypatch.setattr(bootstrap_module, "Session", factory)
    monkeypatch.setattr(BlobStore, "ensure_bucket", fake_bucket)

    async def fake_throttle(username, session):
        return None

    from app import main as main_module
    monkeypatch.setattr(main_module, "throttle_login", fake_throttle)
    asyncio.run(bootstrap_module.bootstrap())
    signed_in = client.post("/api/v1/auth/login", json={
        "username": "new-admin@example.test", "password": "VeryStrong-password-2026",
    })
    assert signed_in.status_code == 200

    caps = client.get("/api/v1/capabilities", headers=auth(admin))
    assert caps.status_code == 200
    data = caps.json()
    assert data["provider_mode"] == "receita"
    assert data["official_configuration"]["ready"] is False
    assert data["environments"] == []
    assert all(not item["enabled"] for item in data["resources"].values())
    assert client.get("/api/v1/companies", headers=auth(admin)).status_code == 200
    created = client.post("/api/v1/queries", json={
        "company_id": company.id, "environment": "production", "resource": "creditos",
    }, headers=auth(admin) | {"Idempotency-Key": "official-without-credentials"})
    assert created.status_code == 409
    assert client.get("/api/v1/queries", headers=auth(admin)).status_code == 200
    assert capabilities()["environments"] == []


def test_production_blocks_explicit_fixture_and_never_falls_back_to_it():
    from app.config import Settings
    safe = Settings(_env_file=None, app_env="production", provider_mode="disabled",
                    secret_key="secure-production-secret-12345678901234567890")
    safe.validate_security()
    with pytest.raises(RuntimeError, match="não permite PROVIDER_MODE=fixture"):
        Settings(_env_file=None, app_env="production", provider_mode="fixture",
                 secret_key="secure-production-secret-12345678901234567890").validate_security()
