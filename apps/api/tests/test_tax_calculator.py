"""Guard company isolation, versioned engine results and fail-closed calculation."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import tax_calculator_api, tax_calculator
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Base, Company, Organization, TaxCalculation, User
from app.security import hash_password, issue_token

VALID_PAYLOAD = {
    "dataHoraEmissao": "2026-10-25T12:00:00-03:00",
    "uf": "BA",
    "municipio": 2901007,
    "itens": [{
        "numero": 1, "ncm": "01012100", "cst": "000", "cClassTrib": "000001",
        "baseCalculo": 100, "quantidade": 1, "unidade": "UN",
    }],
}
ENGINE_RESULT = {
    "total": {"tribCalc": {"IBSCBSTot": {
        "gCBS": {"vCBS": "9.00"}, "gIBS": {"vIBS": "1.00"},
    }}},
    "memoriaCalculo": "Retorno do motor local",
}
VERSION = {"versaoApp": "1.7.0", "versaoDb": "V0061"}


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "long-ephemeral-only-for-calculate-test-value-2026")
    monkeypatch.setenv("PROVIDER_MODE", "fixture")
    monkeypatch.setenv("TAX_CALCULATOR_REGULAR_URL", "http://localhost:8080/api/calculadora/regime-geral")
    monkeypatch.setenv("TAX_CALCULATOR_REGULAR_VERSION_URL", "http://localhost:8080/api/calculadora/dados-abertos/versao")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override

    async def seed():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session, session.begin():
            org_a, org_b = Organization(name="Tenant A"), Organization(name="Tenant B")
            session.add_all([org_a, org_b])
            await session.flush()
            user_a = User(organization_id=org_a.id, username="a@tax.example",
                          password_hash=hash_password("LongPassword-123"), role="admin")
            user_b = User(organization_id=org_b.id, username="b@tax.example",
                          password_hash=hash_password("LongPassword-123"), role="admin")
            session.add_all([user_a, user_b])
            await session.flush()
            company_a = Company(organization_id=org_a.id, name="Empresa A",
                                cnpj="12345678000195", cnpj_base="12345678")
            company_b = Company(organization_id=org_b.id, name="Empresa B",
                                cnpj="87654321000198", cnpj_base="87654321")
            session.add_all([company_a, company_b])
            await session.flush()
            return user_a, user_b, company_a, company_b

    user_a, user_b, company_a, company_b = asyncio.run(seed())

    async def fake_calculate(regime, payload):
        assert regime == "regular"
        assert payload == VALID_PAYLOAD
        return ENGINE_RESULT, VERSION, {"cbs": "9.00", "ibs": "1.00"}

    monkeypatch.setattr(tax_calculator_api, "execute_calculator", fake_calculate)
    try:
        with TestClient(app) as client:
            yield client, issue_token(user_a), issue_token(user_b), company_a, company_b, factory
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
        get_settings.cache_clear()


def bearer(token: str) -> dict:
    return {"Authorization": "Bearer " + token}


def test_versioned_calculation_history_and_immutable_return(clients):
    client, token, _, company, _, _ = clients
    base = {"company_id": company.id, "regime": "regular", "payload": VALID_PAYLOAD}
    headers = bearer(token) | {"Idempotency-Key": "calc-unique-2026"}
    first = client.post("/api/v1/tax-calculations", json=base, headers=headers)
    assert first.status_code == 201, first.text
    data = first.json()
    assert data["totals"] == {"cbs": "9.00", "ibs": "1.00"}
    assert data["engine_version"] == VERSION
    assert data["government_assessment"] is False
    assert data["fiscal_credit_appropriated"] is False
    assert data["engine_response"] == ENGINE_RESULT
    assert len(data["response_sha256"]) == 64
    again = client.post("/api/v1/tax-calculations", json=base, headers=headers)
    assert again.status_code == 200
    assert again.json()["id"] == data["id"]
    assert client.post("/api/v1/tax-calculations",
        json=base | {"regime": "simples"}, headers=headers).status_code == 409
    history = client.get("/api/v1/tax-calculations?company_id=" + company.id, headers=bearer(token))
    assert history.status_code == 200
    assert len(history.json()["items"]) == 1
    assert "engine_response" not in history.json()["items"][0]
    detail = client.get("/api/v1/tax-calculations/" + data["id"], headers=bearer(token))
    assert detail.status_code == 200 and detail.json()["request"] == VALID_PAYLOAD


def test_no_access_outside_organization(clients):
    client, a, b, company_a, _, _ = clients
    created = client.post("/api/v1/tax-calculations",
        json={"company_id": company_a.id, "regime": "regular", "payload": VALID_PAYLOAD},
        headers=bearer(a))
    assert created.status_code == 201, created.text
    assert client.get("/api/v1/tax-calculations/" + created.json()["id"],
        headers=bearer(b)).status_code == 404
    assert client.get("/api/v1/tax-calculations?company_id=" + company_a.id,
        headers=bearer(b)).status_code == 404
    assert client.post("/api/v1/tax-calculations",
        json={"company_id": company_a.id, "regime": "regular", "payload": VALID_PAYLOAD},
        headers=bearer(b)).status_code == 404


def test_input_validation_and_no_guessed_values(clients, monkeypatch):
    client, token, _, company, _, _ = clients
    source = {"company_id": company.id, "regime": "regular", "payload": VALID_PAYLOAD}
    invalid = source | {"payload": VALID_PAYLOAD | {"itens": []}}
    assert client.post("/api/v1/tax-calculations", json=invalid,
                       headers=bearer(token)).status_code == 422

    async def unavailable(regime, payload):
        raise HTTPException(503, "Motor tributário local indisponível")
    monkeypatch.setattr(tax_calculator_api, "execute_calculator", unavailable)
    failure = client.post("/api/v1/tax-calculations", json=source, headers=bearer(token))
    assert failure.status_code == 503
    values = client.get("/api/v1/tax-calculations?company_id=" + company.id,
                        headers=bearer(token))
    assert values.status_code == 200 and values.json()["items"] == []


def test_reject_untrusted_calculator_url():
    for bad in ("https://example.com/api/calculadora/regime-geral",
                "http://169.254.169.254:8080/api/credentials",
                "http://localhost:8080/api/calculadora/regime-geral?override=x",
                "http://localhost:8080/"):
        with pytest.raises(HTTPException) as raised:
            tax_calculator.validate_local_url(bad)
        assert raised.value.status_code == 503


@pytest.mark.asyncio
async def test_engine_response_is_required_and_values_are_never_invented(monkeypatch):
    monkeypatch.setenv("TAX_CALCULATOR_REGULAR_URL", "http://localhost:8080/api/calculadora/regime-geral")
    monkeypatch.setenv("TAX_CALCULATOR_REGULAR_VERSION_URL", "http://localhost:8080/api/calculadora/dados-abertos/versao")
    get_settings.cache_clear()
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=VERSION)
        return httpx.Response(200, json={"total": {}})

    def mock_client(**options):
        return real_client(transport=httpx.MockTransport(handler), **options)

    monkeypatch.setattr(tax_calculator.httpx, "AsyncClient", mock_client)
    try:
        with pytest.raises(HTTPException) as raised:
            await tax_calculator.execute_calculator("regular", VALID_PAYLOAD)
        assert raised.value.status_code == 502
    finally:
        get_settings.cache_clear()
