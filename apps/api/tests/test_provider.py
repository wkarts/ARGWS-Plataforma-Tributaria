import asyncio

import pytest

from app.config import Settings
from app.provider import ReceitaProvider


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def incr(self, key):
        self.data[key] = self.data.get(key, 0) + 1
        return self.data[key]

    async def expire(self, key, ttl):
        return True

    async def eval(self, script, count, key, value):
        if self.data.get(key) == value:
            self.data.pop(key, None)
            return 1
        return 0

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_oauth_token_is_shared_across_provider_instances(monkeypatch):
    from app import provider as module

    settings = Settings(cbs_token_url="https://api.receitafederal.gov.br/token",
        cbs_client_id="client", cbs_client_secret="private")
    redis = FakeRedis()
    calls = 0

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "only-one-token", "expires_in": 3600}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return FakeResponse()

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeClient)
    first = ReceitaProvider(settings)
    second = ReceitaProvider(settings)
    monkeypatch.setattr(first, "redis", lambda: redis)
    monkeypatch.setattr(second, "redis", lambda: redis)
    result = await asyncio.gather(first.token(), second.token())
    assert result == ["only-one-token", "only-one-token"]
    assert calls == 1
    assert await first.token() == "only-one-token" and calls == 1
