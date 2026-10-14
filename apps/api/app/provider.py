from __future__ import annotations

from datetime import datetime, timezone
import asyncio
from hashlib import sha256
import ipaddress
import json
import socket
import secrets
from urllib.parse import quote, urlsplit

import httpx
from redis.asyncio import Redis

from app.config import Settings


class ProviderError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, uncertain: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.uncertain = uncertain


class FixtureProvider:
    async def open(self, resource: str, base: str, environment: str, webhook: str) -> str:
        return f"fixture-{resource}-{base}-{webhook.rsplit('/', 2)[-2]}"

    async def poll(self, ticket: str, environment: str) -> dict:
        return {"estado": "CONCLUIDA"}

    async def download(self, ticket: str, resource: str, base: str, situation: dict) -> bytes:
        item = {"origem": 1, "documento": 55, "chave": "FIXTURE-EXEMPLO", "cbs": {"apurado": 100.10}}
        if resource == "creditos":
            item["cbs"]["apropriacao"] = {"aApropriar": 75.10, "apropriado": 25.00,
                "utilizacao": {"utilizado": 10.00}}
        if resource in {"pagamentos", "recolhimentos"}:
            item = {"numeroDARF": "FIXTURE", "composicao": [{"pa": "10/2026", "total": 42.10}]}
        body = {"tiqueteSolicitacao": ticket, "ni": base,
                "geradoEm": datetime.now(timezone.utc).isoformat(),
                "apuracao": [{"pa": "10/2026"} | {"pagamentos" if resource == "recolhimentos" else resource: [item]}]}
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ReceitaProvider:
    """Restricted and production endpoints published in the CBS v2 API docs.

    OAuth token endpoint and client credentials must be supplied by the operator.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        identity = f"{settings.cbs_token_url}:{settings.cbs_client_id}"
        self.cache_key = "receita:oauth:" + sha256(identity.encode()).hexdigest()

    def redis(self) -> Redis:
        return Redis(host=self.settings.redis_host, port=6379,
                     password=self.settings.redis_password or None,
                     socket_connect_timeout=2, decode_responses=True)

    def base_url(self, environment: str) -> str:
        paths = {
            "restricted": "https://api.receitafederal.gov.br/apuracao-cbs-prr",
            "production": "https://api.receitafederal.gov.br/apuracao-cbs",
        }
        if environment not in paths:
            raise ProviderError("Ambiente oficial inválido")
        return paths[environment]

    async def token(self) -> str:
        # The operator supplies the actual Receita Integra token URL. Client
        # credentials are never echoed in errors or logs.
        parsed = urlsplit(self.settings.cbs_token_url)
        if parsed.scheme != "https" or parsed.hostname != "api.receitafederal.gov.br":
            raise ProviderError("Endpoint OAuth fora do domínio permitido")
        redis = self.redis()
        lock_key = self.cache_key + ":lock"
        try:
            cached = await redis.get(self.cache_key)
            if cached:
                return cached
            for _ in range(40):
                owner = secrets.token_urlsafe(24)
                claimed = await redis.set(lock_key, owner, nx=True, ex=30)
                if claimed:
                    try:
                        cached = await redis.get(self.cache_key)
                        if cached:
                            return cached
                        # One issuance budget shared by all worker processes.
                        issued = await redis.incr(self.cache_key + ":issued")
                        if issued == 1:
                            await redis.expire(self.cache_key + ":issued", 3600)
                        if issued > 2:
                            raise ProviderError("Limite horário de tokens atingido", retryable=True)
                        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                            response = await client.post(
                                self.settings.cbs_token_url,
                                data={"grant_type": "client_credentials"},
                                auth=(self.settings.cbs_client_id, self.settings.cbs_client_secret),
                            )
                            response.raise_for_status()
                            payload = response.json()
                            token = payload.get("access_token")
                            if not isinstance(token, str) or not token:
                                raise ProviderError("Resposta OAuth sem access_token", retryable=True)
                            ttl = max(1, min(int(payload.get("expires_in", 3600)) - 120, 3300))
                            await redis.set(self.cache_key, token, ex=ttl)
                            return token
                    finally:
                        # Do not release a lock replaced by another worker.
                        await redis.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end", 1, lock_key, owner)
                await asyncio.sleep(0.25)
                cached = await redis.get(self.cache_key)
                if cached:
                    return cached
            raise ProviderError("Timeout aguardando token compartilhado", retryable=True)
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError, TypeError) as exc:
            raise ProviderError("Falha na autenticação Receita Integra", retryable=True) from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("Cache OAuth indisponível", retryable=True) from None
        finally:
            await redis.aclose()

    async def authorized(self, method: str, url: str, **kwargs) -> httpx.Response:
        token = await self.token()
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
                response = await client.request(method, url, headers=headers, **kwargs)
            if response.status_code != 401 or attempt:
                return response
            redis = self.redis()
            try:
                await redis.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end",
                                 1, self.cache_key, token)
            finally:
                await redis.aclose()
            token = await self.token()
        return response

    async def open(self, resource: str, base: str, environment: str, webhook: str) -> str:
        url = f"{self.base_url(environment)}/v2/{resource}/{quote(base, safe='')}"
        try:
            response = await self.authorized("POST", url, json={"urlRetorno": webhook})
            if response.status_code != 201:
                raise ProviderError(f"Abertura recusada pela Receita (HTTP {response.status_code})",
                    retryable=response.status_code in {429}, uncertain=response.status_code >= 500)
            ticket = response.json().get("tiqueteSolicitacao")
            if not isinstance(ticket, str) or not 1 <= len(ticket) <= 200:
                raise ProviderError("Tíquete inválido na resposta oficial", uncertain=True)
            return ticket
        except httpx.RequestError:
            # A remote opening may have succeeded despite a timeout. Never
            # auto-retry the POST without a remote idempotency guarantee.
            raise ProviderError("Resultado da abertura indeterminado", uncertain=True) from None
        except ValueError:
            raise ProviderError("Resposta de abertura ilegível", uncertain=True) from None

    async def poll(self, ticket: str, environment: str) -> dict:
        url = f"{self.base_url(environment)}/v2/situacao/{quote(ticket, safe='')}"
        try:
            response = await self.authorized("GET", url)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("estado") not in {"PENDENTE", "EM_PROCESSAMENTO", "CONCLUIDA", "ERRO"}:
                raise ProviderError("Situação oficial inválida", retryable=True)
            return result
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
            raise ProviderError("Consulta da situação indisponível", retryable=True) from None

    async def download(self, ticket: str, resource: str, base: str, situation: dict) -> bytes:
        url = situation.get("urlAssinada")
        expiration = situation.get("urlAssinadaExpiraEm")
        if not isinstance(url, str) or not isinstance(expiration, str):
            raise ProviderError("Arquivo ainda indisponível")
        try:
            expires = datetime.fromisoformat(expiration.replace("Z", "+00:00"))
        except ValueError:
            raise ProviderError("Expiração da URL inválida") from None
        if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
            raise ProviderError("URL assinada expirada")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.settings.download_hosts
                or parsed.username or parsed.password or parsed.port not in {None, 443}):
            raise ProviderError("Host da URL assinada não permitido")
        # Reject private resolutions; TLS hostname verification and an exact
        # host allowlist remain mandatory if DNS changes between checks.
        try:
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(addr[4][0]).is_global for addr in addresses):
                raise ProviderError("Endereço de download não público")
        except socket.gaierror:
            raise ProviderError("Host de download sem resolução") from None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=90), follow_redirects=False) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise ProviderError("Arquivo oficial indisponível", retryable=response.status_code >= 500)
                    chunks = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > self.settings.cbs_max_download_bytes:
                            raise ProviderError("Arquivo acima do limite configurado")
                        chunks.append(chunk)
                    return b"".join(chunks)
        except httpx.RequestError:
            raise ProviderError("Falha no download do arquivo oficial", retryable=True) from None


def provider_for(environment: str, settings: Settings):
    if environment == "fixture" and settings.provider_mode == "fixture":
        return FixtureProvider()
    if environment in {"restricted", "production"} and settings.provider_mode == "receita":
        return ReceitaProvider(settings)
    raise ProviderError("Ambiente indisponível nesta implantação")
