"""The opt-in local CBS path uses SQLite and a simulated official service."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from app.config import Settings
from app.self_cbs_runner import exclusive_lock


API_ROOT = Path(__file__).resolve().parents[1]


def run(arguments: list[str], env: dict[str, str]) -> str:
    result = subprocess.run([sys.executable, *arguments], cwd=API_ROOT, env=env,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_local_mode_is_explicit_and_callback_must_be_public(tmp_path):
    secret = tmp_path / "client-secret"
    secret.write_text("secret-from-authorized-matrix\n")
    arguments = dict(deployment_mode="self", provider_mode="receita",
                     secret_key="local-signing-key-only-for-tests-123456789",
                     cbs_token_url="https://api.receitafederal.gov.br/oauth/token",
                     cbs_client_id="matrix-client", cbs_client_secret_file=str(secret),
                     cbs_credential_organization_id="org", cbs_allowed_bases="12345678",
                     cbs_restricted_enabled=True)
    with pytest.raises(RuntimeError, match="hostname HTTPS público"):
        Settings(**arguments, cbs_webhook_base_url="https://127.0.0.1").validate_security()
    settings = Settings(**arguments, cbs_webhook_base_url="https://callback.example")
    settings.validate_security()
    assert settings.cbs_client_secret == "secret-from-authorized-matrix"
    with pytest.raises(RuntimeError, match="CBS_CLIENT_SECRET_FILE"):
        Settings(**(arguments | {"cbs_client_secret_file": "",
                                 "cbs_client_secret": "secret-in-environment"}),
                 cbs_webhook_base_url="https://callback.example").validate_security()
    with pytest.raises(RuntimeError, match="Fixture"):
        Settings(deployment_mode="self", provider_mode="fixture",
                 secret_key=arguments["secret_key"]).validate_security()


def test_only_one_local_runner_can_hold_the_volume_lock(tmp_path):
    import os

    first = exclusive_lock(tmp_path / ".cbs-worker.lock")
    try:
        with pytest.raises(RuntimeError, match="Outro processador CBS"):
            exclusive_lock(tmp_path / ".cbs-worker.lock")
    finally:
        os.close(first)
    second = exclusive_lock(tmp_path / ".cbs-worker.lock")
    os.close(second)


def test_self_restricted_query_compares_simulated_receipt_with_xml(tmp_path):
    signing = tmp_path / "signing-key"
    password = tmp_path / "admin-password"
    oauth = tmp_path / "cbs-client-secret"
    signing.write_text("local-secret-key-for-this-isolated-process-123456789\n")
    password.write_text("StrongPassword-123\n")
    oauth.write_text("credential-owned-by-this-simulated-matrix\n")
    data = tmp_path / "persistent"
    env = os.environ.copy() | {
        "DEPLOYMENT_MODE": "self", "SELF_DATA_DIR": str(data), "PROVIDER_MODE": "disabled",
        "SECRET_KEY_FILE": str(signing), "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test",
        "BOOTSTRAP_ADMIN_PASSWORD_FILE": str(password), "PYTHONPATH": str(API_ROOT),
    }
    run(["-m", "app.self_setup"], env)
    with sqlite3.connect(data / "fiscal.sqlite3") as connection:
        organization = connection.execute("SELECT id FROM organizations").fetchone()[0]
    env |= {
        "PROVIDER_MODE": "receita", "CBS_RESTRICTED_ENABLED": "true",
        "CBS_CLIENT_SECRET_FILE": str(oauth),
        "CBS_TOKEN_URL": "https://api.receitafederal.gov.br/oauth/token",
        "CBS_CLIENT_ID": "matrix-client", "CBS_WEBHOOK_BASE_URL": "https://callback.example",
        "CBS_CREDENTIAL_ORGANIZATION_ID": organization, "CBS_ALLOWED_BASES": "12345678",
    }
    run(["-c", textwrap.dedent('''
        import asyncio
        from datetime import datetime, timedelta, timezone
        from hashlib import sha256
        import json
        import sqlite3
        from fastapi.testclient import TestClient

        from app import provider, worker
        from app.auto_sync import schedule_due
        from app.config import get_settings
        from app.main import app
        from app.security import webhook_token

        key = '1' * 44
        cnpj = '12345678000195'
        xml = (f'<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">'
               f'<NFe><infNFe Id="NFe{key}"><ide><mod>55</mod>'
               '<dhEmi>2026-10-14T10:30:00-03:00</dhEmi></ide>'
               f'<emit><CNPJ>{cnpj}</CNPJ></emit><dest><CNPJ>22222222222222</CNPJ></dest>'
               '<total><IBSCBSTot><gCBS><vCBS>0.90</vCBS></gCBS>'
               '<gIBS><vIBS>0.10</vIBS></gIBS></IBSCBSTot></total></infNFe>'
               '<Signature xmlns="http://www.w3.org/2000/09/xmldsig#" /></NFe>'
               f'<protNFe><infProt><chNFe>{key}</chNFe><cStat>100</cStat>'
               '</infProt></protNFe></nfeProc>').encode()
        receipt = json.dumps({'tiqueteSolicitacao': 'simulated-ticket', 'ni': '12345678',
            'apuracao': [{'pa': '10/2026', 'debitos': [{'origem': 0, 'documento': 55,
                'chave': key, 'cbs': {'apurado': '0.90'}}]}]}).encode()
        calls = {'token': 0, 'open': 0, 'status': 0, 'download': 0}

        class Response:
            def __init__(self, status_code, payload=None, data=b''):
                self.status_code, self.payload, self.data = status_code, payload, data
            def raise_for_status(self):
                assert self.status_code < 400
            def json(self):
                return self.payload
            async def aiter_bytes(self):
                yield self.data

        class Stream:
            async def __aenter__(self):
                calls['download'] += 1
                return Response(200, data=receipt)
            async def __aexit__(self, *args):
                pass

        class Client:
            def __init__(self, *args, **kwargs):
                assert kwargs.get('follow_redirects') is False
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, url, **kwargs):
                assert url == get_settings().cbs_token_url
                assert kwargs['auth'] == ('matrix-client', 'credential-owned-by-this-simulated-matrix')
                calls['token'] += 1
                return Response(200, {'access_token': 'simulated-bearer', 'expires_in': 3600})
            async def request(self, method, url, **kwargs):
                assert kwargs['headers']['Authorization'] == 'Bearer simulated-bearer'
                if method == 'POST':
                    calls['open'] += 1
                    assert '/apuracao-cbs-prr/v2/debitos/12345678' in url
                    assert kwargs['json']['urlRetorno'].startswith('https://callback.example/api/v1/webhooks/cbs/')
                    return Response(201, {'tiqueteSolicitacao': 'simulated-ticket'})
                assert method == 'GET' and '/v2/situacao/simulated-ticket' in url
                calls['status'] += 1
                return Response(200, {'estado': 'CONCLUIDA',
                    'urlAssinada': 'https://storagegw.estaleiro.serpro.gov.br/receipt.json',
                    'urlAssinadaExpiraEm': (datetime.now(timezone.utc) +
                        timedelta(hours=1)).isoformat()})
            def stream(self, method, url):
                assert method == 'GET' and url.startswith('https://storagegw.estaleiro.serpro.gov.br/')
                return Stream()

        provider.httpx.AsyncClient = Client
        provider.socket.getaddrinfo = lambda *args, **kwargs: [(None, None, None, None, ('1.1.1.1', 443))]
        with TestClient(app) as client:
            login = client.post('/api/v1/auth/login', json={'username': 'admin@example.test',
                'password': 'StrongPassword-123'})
            assert login.status_code == 200, login.text
            auth = {'Authorization': 'Bearer ' + login.json()['access_token']}
            caps = client.get('/api/v1/capabilities', headers=auth).json()
            assert caps['provider_mode'] == 'receita' and caps['environments'] == ['restricted']
            company = client.post('/api/v1/companies', headers=auth,
                json={'name': 'Cliente A', 'cnpj': cnpj}).json()['id']
            agent = client.post('/api/v1/agents', headers=auth,
                json={'company_id': company, 'name': 'Agente'}).json()['token']
            upload = client.post('/api/v1/documents/ingest', params={'company_id': company},
                content=xml, headers={'Authorization': 'Bearer ' + agent,
                    'Content-Type': 'application/xml', 'X-Content-SHA256': sha256(xml).hexdigest()})
            assert upload.status_code == 201, upload.text
            opened = client.post('/api/v1/queries', headers=auth | {'Idempotency-Key': 'self-cbs-123'},
                json={'company_id': company, 'resource': 'debitos', 'environment': 'restricted'})
            assert opened.status_code == 202, opened.text
            query_id = opened.json()['id']
            assert asyncio.run(worker.run_tick()) == 1
            callback = f'/api/v1/webhooks/cbs/{query_id}/{webhook_token(query_id)}'
            assert client.head(callback).status_code == 200
            assert client.post(callback, json={'tiqueteSolicitacao': 'simulated-ticket'}).status_code == 202
            assert asyncio.run(worker.run_tick()) == 1
            assert client.get(f'/api/v1/queries/{query_id}', headers=auth).json()['status'] == 'completed'
            raw = client.get(f'/api/v1/queries/{query_id}/raw', headers=auth)
            assert raw.status_code == 200 and raw.content == receipt
            preview = client.get('/api/v1/assisted-preview', headers=auth,
                params={'company_id': company, 'period': '2026-10'})
            assert preview.status_code == 200, preview.text
            assert preview.json()['coverage']['official_receipts'] == 1
            assert preview.json()['summary']['match'] == 1
            assert calls == {'token': 1, 'open': 1, 'status': 1, 'download': 1}
            other = client.post('/api/v1/companies', headers=auth,
                json={'name': 'Outra raiz', 'cnpj': '87654321000198'})
            assert other.status_code == 201, other.text
            unauthorized = client.post('/api/v1/queries', headers=auth | {
                'Idempotency-Key': 'other-base-123'}, json={
                    'company_id': other.json()['id'], 'resource': 'debitos',
                    'environment': 'restricted'})
            assert unauthorized.status_code == 403
            sibling = client.post('/api/v1/companies', headers=auth,
                json={'name': 'Filial B', 'cnpj': '12345678000276'})
            assert sibling.status_code == 201, sibling.text
            ambiguous = client.post('/api/v1/queries', headers=auth | {
                'Idempotency-Key': 'branch-blocked-123'}, json={
                    'company_id': sibling.json()['id'], 'resource': 'creditos',
                    'environment': 'restricted'})
            assert ambiguous.status_code == 409 and 'filiais' in ambiguous.text
            scheduled = get_settings().model_copy(update={'cbs_auto_sync_enabled': True})
            assert asyncio.run(schedule_due(scheduled)) == 0
    ''')], env)
    with sqlite3.connect(data / "fiscal.sqlite3") as connection:
        assert connection.execute("SELECT issued FROM oauth_token_budgets").fetchone()[0] == 1

    # A second worker process cannot reuse the in-memory token but respects
    # the durable issuance budget. A third request is conservatively blocked.
    second = textwrap.dedent('''
        import asyncio
        from app import provider
        from app.config import get_settings
        from app.provider import ProviderError, ReceitaProvider
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'access_token': 'after-restart', 'expires_in': 3600}
        class Client:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def post(self, *args, **kwargs): return Response()
        provider.httpx.AsyncClient = Client
        async def check():
            assert await ReceitaProvider(get_settings()).token() == 'after-restart'
            provider._local_tokens.clear()
            try:
                await ReceitaProvider(get_settings()).token()
            except ProviderError as error:
                assert 'Limite horário' in str(error)
            else:
                raise AssertionError('terceira emissão indevida')
            rotated = get_settings().model_copy(update={'cbs_client_secret': 'rotated-secret'})
            try:
                await ReceitaProvider(rotated).token()
            except ProviderError as error:
                assert 'Limite horário' in str(error)
            else:
                raise AssertionError('rotação não pode renovar o orçamento horário')
        asyncio.run(check())
    ''')
    run(["-c", second], env)
    with sqlite3.connect(data / "fiscal.sqlite3") as connection:
        assert connection.execute("SELECT issued FROM oauth_token_budgets").fetchone()[0] == 2
