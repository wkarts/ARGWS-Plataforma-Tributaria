"""Authorization and non-posting guarantees for manual financial registers."""

import asyncio
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import get_session
from app.finance_controls import router
from app.models import (AuditEvent, Base, CommercialInstallment, Company, Organization,
                        Record, StockCreditClaim, User)
from app.security import hash_password, issue_token


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-only-finance-key-longer-than-32-characters")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool, connect_args={"check_same_thread": False})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    application = FastAPI()
    application.include_router(router)

    async def override_session():
        async with factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_session

    async def seed():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session, session.begin():
            org_a, org_b = Organization(name="A"), Organization(name="B")
            session.add_all([org_a, org_b])
            await session.flush()
            company_a = Company(organization_id=org_a.id, name="A",
                cnpj="12345678000195", cnpj_base="12345678")
            company_b = Company(organization_id=org_b.id, name="B",
                cnpj="87654321000198", cnpj_base="87654321")
            admin_a = User(organization_id=org_a.id, username="a@example.invalid",
                password_hash=hash_password("Some-Demo-Password-123"), role="admin")
            admin_b = User(organization_id=org_b.id, username="b@example.invalid",
                password_hash=hash_password("Some-Demo-Password-123"), role="admin")
            viewer = User(organization_id=org_a.id, username="v@example.invalid",
                password_hash=hash_password("Some-Demo-Password-123"), role="viewer")
            session.add_all([company_a, company_b, admin_a, admin_b, viewer])
            await session.flush()
            return (company_a.id, company_b.id, issue_token(admin_a),
                    issue_token(admin_b), issue_token(viewer))

    seeded = asyncio.run(seed())
    try:
        with TestClient(application) as client:
            yield client, seeded, factory
    finally:
        application.dependency_overrides.clear()
        asyncio.run(engine.dispose())
        get_settings.cache_clear()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_stock_claim_is_only_a_draft_and_never_posts_credit(api):
    client, (company, _, admin, _, viewer), factory = api
    payload = {"company_id": company, "inventory_date": "2026-12-31",
               "expected_amount": "123.45", "evidence_ref": "inventario-assinado-2026"}
    created = client.post("/api/v1/stock-credit-claims", json=payload, headers=bearer(admin))
    assert created.status_code == 201, created.text
    claim = created.json()
    assert created.headers["cache-control"] == "no-store"
    assert claim["expected_amount"] == "123.45"
    assert claim["appropriated_amount"] == claim["used_amount"] == "0.00"
    assert claim["status"] == "draft" and claim["evidence_verified"] is False
    assert "apropriação" in claim["basis"]
    listed = client.get(f"/api/v1/stock-credit-claims?company_id={company}",
                        headers=bearer(viewer))
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
    assert listed.json()["items"][0]["id"] == claim["id"]

    async def inspect():
        async with factory() as session:
            stored = await session.scalar(select(StockCreditClaim).where(
                StockCreditClaim.id == claim["id"]))
            records = (await session.scalars(select(Record))).all()
            audit = await session.scalar(select(AuditEvent).where(
                AuditEvent.entity_id == claim["id"]))
            return stored, records, audit

    stored, records, audit = asyncio.run(inspect())
    assert stored.appropriated_amount == stored.used_amount == 0
    assert records == []
    assert audit.action == "stock_credit_claim.created"
    assert "evidence_ref" not in audit.metadata_json


def test_commercial_payment_never_releases_stock_or_official_credit(api):
    client, (company, _, admin, _, _), factory = api
    claim = client.post("/api/v1/stock-credit-claims", json={"company_id": company,
        "inventory_date": "2026-12-31", "expected_amount": "50.00",
        "evidence_ref": "inventario"}, headers=bearer(admin)).json()
    created = client.post("/api/v1/commercial-installments", json={
        "company_id": company, "document_ref": "NF-123", "installment_no": 1,
        "amount": "15.10", "paid_at": "2026-10-01T18:30:00-03:00"}, headers=bearer(admin))
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    payment = created.json()
    assert payment["amount"] == "15.10" and payment["paid_at"] == "2026-10-01T21:30:00Z"
    assert "não comprova extinção" in payment["basis"]
    assert client.get("/api/v1/commercial-installments", headers=bearer(admin)).json()[
        "items"][0]["id"] == payment["id"]
    latest = client.get("/api/v1/stock-credit-claims", headers=bearer(admin)).json()["items"][0]
    assert latest["id"] == claim["id"]
    assert latest["appropriated_amount"] == latest["used_amount"] == "0.00"

    async def inspect():
        async with factory() as session:
            installment = await session.scalar(select(CommercialInstallment).where(
                CommercialInstallment.id == payment["id"]))
            return installment, (await session.scalars(select(Record))).all()

    installment, records = asyncio.run(inspect())
    assert installment.amount == Decimal("15.10") and records == []


def test_tenant_and_role_scope_on_both_registers(api):
    client, (company_a, company_b, admin_a, admin_b, viewer), _ = api
    stock = {"company_id": company_a, "inventory_date": "2026-12-31",
             "expected_amount": "10.00", "evidence_ref": "relatorio"}
    installment = {"company_id": company_a, "document_ref": "NFe-123",
                   "installment_no": 1, "amount": "10.00"}
    for path, body in (("stock-credit-claims", stock),
                       ("commercial-installments", installment)):
        url = "/api/v1/" + path
        assert client.post(url, json=body, headers=bearer(viewer)).status_code == 403
        assert client.post(url, json=body, headers=bearer(admin_b)).status_code == 404
        assert client.post(url, json=body).status_code == 401
        assert client.post(url, json=body, headers=bearer(admin_a)).status_code == 201
        assert len(client.get(url, headers=bearer(viewer)).json()["items"]) == 1
        assert client.get(url, headers=bearer(admin_b)).json()["items"] == []
        assert client.get(url + f"?company_id={company_a}", headers=bearer(admin_b)).status_code == 404
        assert client.get(url + f"?company_id={company_b}", headers=bearer(admin_a)).status_code == 404
        assert client.get(url).status_code == 401


@pytest.mark.parametrize("bad_value", ["0", "-1.00", "1.001", "NaN", "Infinity", 0.1,
                                       "10000000000000000", "1e4", " "])
def test_amounts_must_be_exact_positive_numeric_18_2(api, bad_value):
    client, (company, _, admin, _, _), _ = api
    payloads = (
        ("stock-credit-claims", {"company_id": company, "inventory_date": "2026-12-31",
                                 "expected_amount": bad_value, "evidence_ref": "inventario"}),
        ("commercial-installments", {"company_id": company, "document_ref": "NF-123",
                                     "installment_no": 1, "amount": bad_value}),
    )
    for path, payload in payloads:
        result = client.post("/api/v1/" + path, json=payload, headers=bearer(admin))
        assert result.status_code == 422, (path, bad_value, result.text)


def test_invalid_dates_document_issuer_and_write_only_fields(api):
    client, (company, _, admin, _, _), _ = api
    stock = {"company_id": company, "inventory_date": "2026-02-29",
             "expected_amount": "10", "evidence_ref": "inventario"}
    assert client.post("/api/v1/stock-credit-claims", json=stock,
                       headers=bearer(admin)).status_code == 422
    stock["inventory_date"] = "2026-12-31"
    for extra in ({"appropriated_amount": "10"}, {"used_amount": "10"},
                  {"status": "approved"}, {"organization_id": "other-org"}):
        assert client.post("/api/v1/stock-credit-claims", json=stock | extra,
                           headers=bearer(admin)).status_code == 422
    payment = {"company_id": company, "document_ref": "NF-123",
               "installment_no": 1, "amount": "10.00"}
    for bad_date in ("2026-10-01", "2026-02-29T12:00:00-03:00",
                     "2026-10-01T12:00:00", "2026-10-01T12:00:00+25:00"):
        assert client.post("/api/v1/commercial-installments",
            json=payment | {"paid_at": bad_date}, headers=bearer(admin)).status_code == 422
    invalid_issuer_key = "35" + "2610" + "11111111111111" + "55" + "001" + "000000001" + "1" + "00000001" + "0"
    assert len(invalid_issuer_key) == 44
    assert client.post("/api/v1/commercial-installments",
        json=payment | {"document_ref": invalid_issuer_key},
        headers=bearer(admin)).status_code == 422
    for bad in (payment | {"installment_no": 0}, payment | {"tax_extinguished": True},
                payment | {"credit_appropriated": "10"},
                payment | {"document_ref": "\n"}):
        assert client.post("/api/v1/commercial-installments", json=bad,
                           headers=bearer(admin)).status_code == 422


def test_pagination_does_not_cross_company_or_tenant(api):
    client, (company, _, admin, admin_other, _), _ = api
    for index in range(3):
        result = client.post("/api/v1/commercial-installments", json={
            "company_id": company, "document_ref": f"NF-{index}", "installment_no": index + 1,
            "amount": "1.00"}, headers=bearer(admin))
        assert result.status_code == 201
    first = client.get(f"/api/v1/commercial-installments?company_id={company}&limit=2",
                       headers=bearer(admin)).json()
    assert len(first["items"]) == 2 and first["next_cursor"] == "2"
    second = client.get(f"/api/v1/commercial-installments?company_id={company}&limit=2&cursor=2",
                        headers=bearer(admin)).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert len({item["id"] for item in first["items"] + second["items"]}) == 3
    assert client.get("/api/v1/commercial-installments?cursor=-1",
                      headers=bearer(admin)).status_code == 422
    assert client.get("/api/v1/commercial-installments",
                      headers=bearer(admin_other)).json()["items"] == []
