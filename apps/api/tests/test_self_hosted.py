"""Exercise a clean local installation through setup, API and durable storage."""

from __future__ import annotations

import os
from pathlib import Path
import importlib.util
import sqlite3
import subprocess
import sys
import textwrap


API_ROOT = Path(__file__).resolve().parents[1]


def test_installation_secret_generator_keeps_files_private(tmp_path, monkeypatch):
    script = API_ROOT.parents[1] / "deploy" / "self" / "init_secrets.py"
    spec = importlib.util.spec_from_file_location("self_init_secrets", script)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(installer, "HERE", tmp_path)
    monkeypatch.setattr(installer.getpass, "getpass", lambda prompt: "StrongPassword-123")
    monkeypatch.setattr(sys, "argv", [str(script), "--project", "tenant-a",
                                  "--email", "admin@example.test"])
    installer.main()
    assert (tmp_path / ".env").read_text().startswith("COMPOSE_PROJECT_NAME=tenant-a\n")
    assert len((tmp_path / "secrets" / "signing-key").read_text().strip()) >= 32
    assert (tmp_path / "secrets" / "admin-password").read_text().strip() == "StrongPassword-123"
    monkeypatch.setattr(installer.getpass, "getpass", lambda prompt: "AuthorizedMatrixSecret-123")
    monkeypatch.setattr(sys, "argv", [str(script), "--set-cbs-client-secret"])
    installer.main()
    assert (tmp_path / "secrets" / "cbs-client-secret").read_text().strip() == "AuthorizedMatrixSecret-123"
    if os.name != "nt":
        assert all(path.stat().st_mode & 0o077 == 0 for path in
                   (tmp_path / ".env", *(tmp_path / "secrets").iterdir()))
    monkeypatch.setattr(sys, "argv", [str(script), "--erase-bootstrap-password"])
    installer.main()
    assert (tmp_path / "secrets" / "admin-password").read_text() == ""


def test_root_entrypoint_stages_secrets_then_drops_privileges(tmp_path, monkeypatch):
    script = API_ROOT.parents[1] / "deploy" / "self" / "prepare_secrets.py"
    spec = importlib.util.spec_from_file_location("self_prepare_secrets", script)
    entrypoint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entrypoint)
    monkeypatch.setattr(entrypoint, "SECRET_DIR", tmp_path / "run" / "app-secrets")
    signing = tmp_path / "signing-key"
    password = tmp_path / "admin-password"
    signing.write_text("secret-key-test-value\n")
    password.write_text("one-time-password\n")
    cbs = tmp_path / "cbs-client-secret"
    cbs.write_text("credential\n")
    signing.chmod(0o600)
    password.chmod(0o600)
    cbs.chmod(0o600)
    monkeypatch.setenv("SECRET_KEY_FILE", str(signing))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD_FILE", str(password))
    monkeypatch.setenv("CBS_CLIENT_SECRET_FILE", str(cbs))
    events = []
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 0)
    monkeypatch.setattr(entrypoint.os, "chown", lambda *args: events.append(("chown", args[1:])))
    monkeypatch.setattr(entrypoint.os, "fchown", lambda *args: events.append(("fchown", args[1:])))
    monkeypatch.setattr(entrypoint.os, "setgroups", lambda groups: events.append(("groups", groups)))
    monkeypatch.setattr(entrypoint.os, "setgid", lambda gid: events.append(("gid", gid)))
    monkeypatch.setattr(entrypoint.os, "setuid", lambda uid: events.append(("uid", uid)))

    class Executed(Exception):
        pass

    def capture_exec(program, args, environment):
        events.append(("exec", program, args, dict(environment)))
        raise Executed

    monkeypatch.setattr(entrypoint.os, "execvpe", capture_exec)
    import pytest
    with pytest.raises(Executed):
        entrypoint.main(["python", "-m", "app.self_setup"])
    assert events[-4:-1] == [("groups", []), ("gid", 10001), ("uid", 10001)]
    staged = [entrypoint.SECRET_DIR / "signing-key", entrypoint.SECRET_DIR / "admin-password",
              entrypoint.SECRET_DIR / "cbs-client-secret"]
    assert [path.read_text() for path in staged] == ["secret-key-test-value\n", "one-time-password\n",
                                                   "credential\n"]
    assert events[-1][3]["SECRET_KEY_FILE"] == str(staged[0])
    assert events[-1][3]["BOOTSTRAP_ADMIN_PASSWORD_FILE"] == str(staged[1])
    assert events[-1][3]["CBS_CLIENT_SECRET_FILE"] == str(staged[2])
    assert all(path.stat().st_mode & 0o077 == 0 for path in staged)


def run_python(arguments: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([sys.executable, *arguments], cwd=API_ROOT, env=env,
                            capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_self_setup_login_collect_and_reconcile_without_remote_services(tmp_path):
    signing = tmp_path / "signing-key"
    initial = tmp_path / "admin-password"
    signing.write_text("local-secret-key-generated-only-for-this-test-123456789\n")
    initial.write_text("StrongPassword-123\n")
    data = tmp_path / "persistent"
    env = os.environ.copy() | {
        "DEPLOYMENT_MODE": "self", "SELF_DATA_DIR": str(data), "PROVIDER_MODE": "disabled",
        "SECRET_KEY_FILE": str(signing), "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test",
        "BOOTSTRAP_ADMIN_PASSWORD_FILE": str(initial), "PYTHONPATH": str(API_ROOT),
    }
    run_python(["-m", "app.self_setup"], env)
    initial.write_text("")  # Safe to remove the original bootstrap secret after first setup.
    run_python(["-m", "app.self_setup"], env)
    database = data / "fiscal.sqlite3"
    assert database.is_file()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "20261027_0007"
    if os.name != "nt":
        assert data.stat().st_mode & 0o077 == 0
        assert database.stat().st_mode & 0o077 == 0

    run_python(["-c", textwrap.dedent("""
        from concurrent.futures import ThreadPoolExecutor
        from hashlib import sha256
        from fastapi.testclient import TestClient
        from app.main import app

        cnpj = '12345678000195'
        key = '1' * 44
        xml = (f'<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">'
               f'<NFe><infNFe Id="NFe{key}"><ide><mod>55</mod>'
               '<dhEmi>2026-10-14T10:30:00-03:00</dhEmi></ide>'
               f'<emit><CNPJ>{cnpj}</CNPJ></emit><dest><CNPJ>22222222222222</CNPJ></dest>'
               '<total><ICMSTot><vNF>100.00</vNF></ICMSTot>'
               '<IBSCBSTot><gCBS><vCBS>0.90</vCBS></gCBS>'
               '<gIBS><vIBS>0.10</vIBS></gIBS></IBSCBSTot></total></infNFe>'
               '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#" /></NFe>'
               f'<protNFe><infProt><chNFe>{key}</chNFe><cStat>100</cStat>'
               '</infProt></protNFe></nfeProc>').encode()
        with TestClient(app) as client:
            assert client.get('/health/ready').status_code == 200
            login = client.post('/api/v1/auth/login', json={
                'username': 'admin@example.test', 'password': 'StrongPassword-123'})
            assert login.status_code == 200, login.text
            headers = {'Authorization': 'Bearer ' + login.json()['access_token']}
            assert client.get('/api/v1/auth/me', headers=headers).status_code == 200
            caps = client.get('/api/v1/capabilities', headers=headers).json()
            assert caps['provider_mode'] == 'disabled' and caps['environments'] == []
            company = client.post('/api/v1/companies', headers=headers,
                json={'name': 'Cliente A', 'cnpj': cnpj})
            assert company.status_code == 201, company.text
            other = client.post('/api/v1/companies', headers=headers,
                json={'name': 'Cliente B', 'cnpj': '87654321000198'})
            assert other.status_code == 201, other.text
            agent = client.post('/api/v1/agents', headers=headers,
                json={'company_id': company.json()['id'], 'name': 'Serviço local'})
            assert agent.status_code == 201, agent.text
            ingest_headers = {'Authorization': 'Bearer ' + agent.json()['token'],
                'Content-Type': 'application/xml', 'X-Content-SHA256': sha256(xml).hexdigest()}
            target = f"/api/v1/documents/ingest?company_id={company.json()['id']}"
            created = client.post(target, content=xml, headers=ingest_headers)
            assert created.status_code == 201, created.text
            assert client.post(target, content=xml, headers=ingest_headers).status_code == 200
            xml2 = xml.replace(b'<vNF>100.00</vNF>', b'<vNF>101.00</vNF>')
            headers2 = ingest_headers | {'X-Content-SHA256': sha256(xml2).hexdigest()}
            with ThreadPoolExecutor(max_workers=4) as executor:
                arrivals = list(executor.map(lambda _: client.post(target, content=xml2,
                    headers=headers2).status_code, range(4)))
            assert sorted(arrivals) == [200, 200, 200, 201], arrivals
            foreign = f"/api/v1/documents/ingest?company_id={other.json()['id']}"
            assert client.post(foreign, content=xml, headers=ingest_headers).status_code == 403
            raw = client.get(f"/api/v1/documents/{created.json()['id']}/raw", headers=headers)
            assert raw.status_code == 200 and raw.content == xml
            report = client.get('/api/v1/reconciliation', headers=headers,
                params={'company_id': company.json()['id'], 'period': '2026-10'})
            assert report.status_code == 200, report.text
            assert report.json()['summary']['ambiguous'] == 1
            assert report.json()['official_coverage_proven'] is False
            query = client.post('/api/v1/queries', headers=headers | {'Idempotency-Key': 'disabled-123'},
                json={'company_id': company.json()['id'], 'resource': 'creditos',
                      'environment': 'fixture'})
            assert query.status_code == 409, query.text
            for _ in range(9):
                assert client.post('/api/v1/auth/login', json={
                    'username': 'admin@example.test', 'password': 'wrong'}).status_code == 401
            assert client.post('/api/v1/auth/login', json={
                'username': 'admin@example.test', 'password': 'wrong'}).status_code == 429
    """)], env)
    assert len(list((data / "blobs" / "documents").rglob("*.xml"))) == 2
