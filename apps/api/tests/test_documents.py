import asyncio
from hashlib import sha256

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import get_session
from app.documents import router
from app.reconciliation_api import router as reconciliation_router
from app.models import Base, Company, Organization, User
from app.security import hash_password, issue_token
from app.storage import BlobStore
from app.xml_parser import parse_fiscal_xml


CNPJ = "11111111111111"  # Synthetic only; database fixtures bypass registration rules.
OTHER_CNPJ = "22222222222222"
KEY = "1" * 44
XML = (f'<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">'
       f'<NFe><infNFe Id="NFe{KEY}"><ide><mod>55</mod>'
       '<dhEmi>2026-10-14T10:30:00-03:00</dhEmi></ide>'
       f'<emit><CNPJ>{CNPJ}</CNPJ></emit><dest><CNPJ>{OTHER_CNPJ}</CNPJ></dest>'
       '<total><ICMSTot><vNF>100.00</vNF></ICMSTot>'
       '<IBSCBSTot><gCBS><vCBS>0.90</vCBS></gCBS>'
       '<gIBS><vIBS>0.10</vIBS></gIBS></IBSCBSTot></total></infNFe>'
       '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#" /></NFe>'
       f'<protNFe><infProt><chNFe>{KEY}</chNFe><cStat>100</cStat>'
       '</infProt></protNFe></nfeProc>').encode()


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-only-signing-key-longer-than-32-characters")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool, connect_args={"check_same_thread": False})
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router)
    app.include_router(reconciliation_router)
    blobs: dict[str, bytes] = {}

    async def override_session():
        async with session_factory() as session:
            yield session

    async def fake_bucket(self):
        return None

    async def fake_put(self, organization_id, company_id, data):
        digest = sha256(data).hexdigest()
        key = f"documents/{organization_id}/{company_id}/{digest}.xml"
        blobs.setdefault(key, data)
        return key, digest

    async def fake_get(self, organization_id, company_id, key, expected_digest, max_bytes):
        assert key.startswith(f"documents/{organization_id}/{company_id}/")
        result = blobs[key]
        if sha256(result).hexdigest() != expected_digest or len(result) > max_bytes:
            raise RuntimeError("Corrompido")
        return result

    monkeypatch.setattr(BlobStore, "ensure_bucket", fake_bucket)
    monkeypatch.setattr(BlobStore, "put_document", fake_put)
    monkeypatch.setattr(BlobStore, "get_document", fake_get)
    app.dependency_overrides[get_session] = override_session

    async def seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session, session.begin():
            a = Organization(name="Demo A")
            b = Organization(name="Demo B")
            session.add_all([a, b])
            await session.flush()
            admin_a = User(organization_id=a.id, username="a@example.invalid",
                password_hash=hash_password("DemoPassword-123"), role="admin", active=True,
                token_version=0)
            admin_b = User(organization_id=b.id, username="b@example.invalid",
                password_hash=hash_password("DemoPassword-123"), role="admin", active=True,
                token_version=0)
            viewer_a = User(organization_id=a.id, username="viewer@example.invalid",
                password_hash=hash_password("DemoPassword-123"), role="viewer", active=True,
                token_version=0)
            company_a = Company(organization_id=a.id, name="Demo A", cnpj=CNPJ,
                cnpj_base=CNPJ[:8])
            company_b = Company(organization_id=b.id, name="Demo B", cnpj=CNPJ,
                cnpj_base=CNPJ[:8])
            company_a2 = Company(organization_id=a.id, name="Demo A2", cnpj=OTHER_CNPJ,
                cnpj_base=OTHER_CNPJ[:8])
            session.add_all([admin_a, admin_b, viewer_a, company_a, company_b, company_a2])
            await session.flush()
            return (issue_token(admin_a), issue_token(admin_b), issue_token(viewer_a),
                    company_a.id, company_b.id, company_a2.id)

    fixture = asyncio.run(seed())
    try:
        with TestClient(app) as client:
            yield client, fixture, blobs
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
        get_settings.cache_clear()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def upload(client, token, company_id, body=XML, **headers):
    return client.post(f"/api/v1/documents/ingest?company_id={company_id}", content=body,
        headers=bearer(token) | {"Content-Type": "application/xml",
                                 "X-Content-SHA256": sha256(body).hexdigest()} | headers)


def test_agent_upload_preserves_xml_deduplicates_and_enforces_scope(api):
    client, (admin_a, admin_b, viewer, company_a, company_b, company_a2), blobs = api
    created = client.post("/api/v1/agents", json={"company_id": company_a, "name": "Coletor"},
                          headers=bearer(admin_a))
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert token.startswith("rta_")
    listed = client.get("/api/v1/agents", headers=bearer(admin_a)).json()["items"]
    assert len(listed) == 1 and "token" not in listed[0] and "token_hash" not in listed[0]
    assert client.get("/api/v1/agents", headers=bearer(admin_b)).json()["items"] == []
    assert client.post("/api/v1/agents", json={"company_id": company_a, "name": "Outro"},
                       headers=bearer(admin_b)).status_code == 404
    assert client.post("/api/v1/agents", json={"company_id": company_a, "name": "Outro"},
                       headers=bearer(viewer)).status_code == 403

    ingested = upload(client, token, company_a)
    assert ingested.status_code == 201, ingested.text
    body = ingested.json()
    assert body["duplicate"] is False
    assert body["sha256"] == sha256(XML).hexdigest()
    assert body["kind"] == "nfe" and body["document_key"] == KEY
    assert body["model"] == "55" and body["period"] == "2026-10"
    assert body["direction"] == "outgoing" and body["cbs_amount"] == "0.90"
    assert body["total_amount"] == "100.00" and body["signature_present"] is True
    assert body["authorization_status"] == "reported_authorized"
    assert upload(client, token, company_a).json()["id"] == body["id"]
    assert upload(client, token, company_a).status_code == 200
    assert len(blobs) == 1
    corrected_xml = XML.replace(b"<vNF>100.00</vNF>", b"<vNF>101.00</vNF>")
    corrected = upload(client, token, company_a, corrected_xml)
    assert corrected.status_code == 201
    assert corrected.json()["id"] != body["id"]
    assert corrected.json()["document_key"] == body["document_key"]
    assert corrected.json()["total_amount"] == "101.00"
    assert len(blobs) == 2  # Same business key, separate immutable byte versions.
    assert upload(client, token, company_a2).status_code == 403
    assert client.get("/api/v1/documents", headers=bearer(token)).status_code == 401

    raw = client.get(f'/api/v1/documents/{body["id"]}/raw', headers=bearer(admin_a))
    assert raw.status_code == 200 and raw.content == XML
    assert raw.headers["x-content-sha256"] == sha256(XML).hexdigest()
    assert raw.headers["cache-control"] == "no-store"
    assert client.get(f'/api/v1/documents/{body["id"]}/raw',
                      headers=bearer(admin_b)).status_code == 404
    assert client.get("/api/v1/documents", headers=bearer(admin_b)).json()["items"] == []
    assert {row["id"] for row in client.get("/api/v1/documents",
           headers=bearer(viewer)).json()["items"]} == {body["id"], corrected.json()["id"]}
    assert len(client.get(f"/api/v1/documents?company_id={company_a}",
                          headers=bearer(admin_a)).json()["items"]) == 2

    other = upload(client, admin_b, company_b)
    assert other.status_code == 201 and other.json()["id"] != body["id"]
    assert len(blobs) == 3  # Same bytes stored separately per organization/company.
    assert client.delete(f'/api/v1/agents/{created.json()["id"]}',
                         headers=bearer(admin_b)).status_code == 404
    assert client.delete(f'/api/v1/agents/{created.json()["id"]}',
                         headers=bearer(admin_a)).json()["revoked"] is True
    assert upload(client, token, company_a).status_code == 401
    stored_key = next(key for key in blobs if key.endswith(f'{body["sha256"]}.xml')
                      and f'/{company_a}/' in key)
    blobs[stored_key] = b"tampered"
    assert client.get(f'/api/v1/documents/{body["id"]}/raw',
                      headers=bearer(admin_a)).status_code == 503


def test_rejects_invalid_xml_hash_mismatch_foreign_company_and_oversize(api):
    client, (admin_a, _, _, company_a, _, _), blobs = api
    assert upload(client, admin_a, company_a, b"<not-xml").status_code == 422
    assert upload(client, admin_a, company_a,
                  f"<Fiscal><CNPJ>{CNPJ}</CNPJ></Fiscal>".encode()).status_code == 422
    assert upload(client, admin_a, company_a, XML, **{"X-Content-SHA256": "0" * 64}).status_code == 422
    assert upload(client, admin_a, company_a, XML, **{"Content-Type": "text/plain"}).status_code == 415
    foreign = XML.replace(CNPJ.encode(), b"33333333333333")
    assert upload(client, admin_a, company_a, foreign).status_code == 422
    assert upload(client, admin_a, company_a, b"x" * (10 * 1024 * 1024 + 1)).status_code == 413
    assert blobs == {}


def test_xml_parser_rejects_dtd_entities_xinclude_and_protocol_mismatch():
    for bad in (
        b'<!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><r>&xxe;</r>',
        b'<r xmlns:xi="http://www.w3.org/2001/XInclude">'
        + f'<CNPJ>{CNPJ}</CNPJ>'.encode() + b'<xi:include href="file:///etc/passwd" /></r>',
        b"<r>" * 129 + b"</r>" * 129,
    ):
        with pytest.raises(ValueError):
            parse_fiscal_xml(bad, CNPJ)
    metadata = parse_fiscal_xml(XML.replace(f"<chNFe>{KEY}</chNFe>".encode(),
                                            f"<chNFe>{'2' * 44}</chNFe>".encode()), CNPJ)
    assert metadata.authorization_status == "protocol_mismatch"
    assert metadata.signature_present  # Presence is deliberately not verification.


def test_reconciliation_endpoint_scopes_company_and_preserves_unknown_coverage(api):
    client, (admin_a, admin_b, _, company_a, _, company_a2), _ = api
    assert upload(client, admin_a, company_a).status_code == 201
    result = client.get(f"/api/v1/reconciliation?company_id={company_a}&period=2026-10",
                        headers=bearer(admin_a))
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["official_coverage_proven"] is False
    assert body["summary"] == {"total": 1, "match": 0, "mismatch": 0,
                                "missing": 0, "ambiguous": 0, "unknown": 1}
    assert body["items"][0]["reason"] == "official_coverage_unverified"
    assert client.get(f"/api/v1/reconciliation?company_id={company_a}&period=2026-10",
                      headers=bearer(admin_b)).status_code == 404
    assert client.get(f"/api/v1/reconciliation?company_id={company_a2}&period=2026-10",
                      headers=bearer(admin_a)).json()["summary"]["total"] == 0
    assert client.get(f"/api/v1/reconciliation?company_id={company_a}&period=2026-13",
                      headers=bearer(admin_a)).status_code == 422


def test_reconciliation_reports_xml_excluded_for_missing_competence(api):
    client, (admin_a, _, _, company_a, _, _), _ = api
    event_xml = (f"<evento><infEvento><CNPJ>{CNPJ}</CNPJ>"
                 f"<chNFe>{KEY}</chNFe></infEvento></evento>").encode()
    assert upload(client, admin_a, company_a, event_xml).status_code == 201
    result = client.get(f"/api/v1/reconciliation?company_id={company_a}&period=2026-10",
                        headers=bearer(admin_a))
    assert result.status_code == 200
    assert result.json()["unperioded_document_count"] == 1
    assert result.json()["summary"]["total"] == 0
