"""Installation identity is public, isolated from companies, and admin-managed."""

import asyncio
from io import BytesIO
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.branding import router
from app.config import get_settings
from app.db import get_session
from app.models import Base, Organization, User
from app.security import hash_password, issue_token


@pytest.fixture
def installation(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "temporary-brand-test-secret-at-least-32-characters")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool, connect_args={"check_same_thread": False})
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = override_session

    async def seed():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session, session.begin():
            first = Organization(name="Instalador")
            other = Organization(name="Outra organização")
            session.add_all([first, other])
            await session.flush()
            users = [
                User(organization_id=first.id, username="admin@example.test",
                     password_hash=hash_password("TestPassword-123"), role="admin"),
                User(organization_id=first.id, username="user@example.test",
                     password_hash=hash_password("TestPassword-123"), role="viewer"),
                User(organization_id=other.id, username="other@example.test",
                     password_hash=hash_password("TestPassword-123"), role="admin"),
            ]
            session.add_all(users)
            await session.flush()
            return [issue_token(user) for user in users]

    tokens = asyncio.run(seed())
    try:
        with TestClient(application) as client:
            yield client, tokens
    finally:
        asyncio.run(engine.dispose())
        get_settings.cache_clear()


def body(version=0, **changes):
    return {"version": version, "display_name": "Escritório Contábil Exemplo",
            "short_name": "Exemplo", "primary_color": "#224466",
            "secondary_color": "#112233", **changes}


def save(client, token, values, logo=None):
    return client.put("/api/v1/installation/branding",
        headers={"Authorization": f"Bearer {token}"},
        data={"payload": json.dumps(values)},
        files={"logo": logo} if logo else None)


def test_public_neutral_until_admin_configures_one_installation(installation):
    client, tokens = installation
    before = client.get("/api/v1/installation/branding")
    assert before.status_code == 200
    assert before.json() == {"display_name": "Gestão Fiscal", "short_name": "Fiscal",
        "primary_color": "#255896", "secondary_color": "#102743", "logo_url": None, "version": 0}
    assert "no-store" in before.headers["cache-control"]
    assert save(client, tokens[1], body()).status_code == 403
    assert save(client, tokens[0], body()).status_code == 200
    assert save(client, tokens[2], body(1, display_name="Marca indevida")).status_code == 403
    assert save(client, tokens[0], body()).status_code == 409
    manifest = client.get("/api/v1/installation/branding/manifest.webmanifest").json()
    assert manifest["name"] == "Escritório Contábil Exemplo"
    assert manifest["icons"][0]["src"].startswith("/api/v1/installation/branding/icon.png")
    assert "#224466" in client.get("/api/v1/installation/branding/theme.css").text
    bootstrap = client.get("/api/v1/installation/branding/bootstrap.js")
    assert bootstrap.status_code == 200 and bootstrap.headers["cache-control"] == "no-store"
    public = json.loads(bootstrap.text.split("window.__INSTALLATION_BRAND__=", 1)[1].split(";document.title", 1)[0])
    assert public["display_name"] == "Escritório Contábil Exemplo"
    assert "owner_organization_id" not in bootstrap.text
    assert "--brand-primary" in bootstrap.text
    assert "manifest.webmanifest?v=" in bootstrap.text
    assert client.get("/api/v1/installation/branding/icon.png?size=512").headers["content-type"] == "image/png"


def test_normalized_logo_no_svg_or_metadata_and_versioned_removal(installation):
    client, tokens = installation
    image = Image.new("RGB", (80, 80), "#204060")
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "não incluir nos arquivos publicados")
    image.save(output, format="PNG", pnginfo=metadata)
    assert save(client, tokens[0], body(), ("logo.png", output.getvalue(), "image/png")).status_code == 200
    result = client.get("/api/v1/installation/branding").json()
    logo = client.get(result["logo_url"])
    assert logo.status_code == 200
    assert logo.headers["x-content-type-options"] == "nosniff"
    with Image.open(BytesIO(logo.content)) as clean:
        assert clean.format == "PNG"
        assert "Comment" not in clean.info
    assert save(client, tokens[0], body(1), ("logo.svg", b'<svg onload="alert(1)"/>', "image/svg+xml")).status_code == 422
    assert save(client, tokens[0], body(1, primary_color="red;url(evil)" )).status_code == 422
    assert client.get(result["logo_url"]).status_code == 200
    assert save(client, tokens[0], body(1, remove_logo=True)).status_code == 200
    assert client.get("/api/v1/installation/branding/logo.png").status_code == 404


def test_brand_name_is_escaped_in_initial_script(installation):
    client, tokens = installation
    name = 'Escritório </script><script>alert(1)</script> & "teste"'
    assert save(client, tokens[0], body(display_name=name)).status_code == 200
    script = client.get("/api/v1/installation/branding/bootstrap.js").text
    assert "</script>" not in script
    assert "\\u003c/script\\u003e" in script


def test_sqlite_migrations_brand_and_auth_are_compatible(tmp_path, monkeypatch):
    database = tmp_path / "installation.sqlite"
    monkeypatch.setenv("DATABASE_URL_OVERRIDE", f"sqlite+aiosqlite:///{database}")
    get_settings.cache_clear()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database}")
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"installation_brand", "installation_brand_assets", "auth_attempts"} <= tables
        assert "companies" in tables
    finally:
        engine.dispose()
        get_settings.cache_clear()
