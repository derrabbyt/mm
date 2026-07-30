from contextlib import asynccontextmanager

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from redis_fastapi import get_async_redis

from app.main import app


class BrokenRedis:
    """A Redis client where every operation fails, as during an outage.

    Injected in place of the real client so the translation in cache/people.py
    actually runs - patching the cache functions themselves would skip it.
    """

    def __getattr__(self, _name):
        async def _fail(*args, **kwargs):
            raise ConnectionError("redis is down")

        return _fail

    # The pipeline() path is sync and used as an async context manager, so it
    # cannot go through __getattr__ above.
    def pipeline(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def set(self, *args, **kwargs):
        return self

    def sadd(self, *args, **kwargs):
        return self

    async def execute(self):
        raise ConnectionError("redis is down")


@asynccontextmanager
async def _client_with(redis):
    """Build a client bound to `redis`, restoring any previous override after.

    Save/restore rather than clear() so that a test using both `client` and
    `broken_client` does not have one fixture wipe the other's override.
    """
    previous = app.dependency_overrides.get(get_async_redis)
    app.dependency_overrides[get_async_redis] = lambda: redis

    # raise_app_exceptions=False so the registered handlers produce a response
    # instead of the exception propagating into the test. Overriding the Redis
    # dependency also lets us skip the lifespan, which would dial a real server.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_async_redis, None)
        else:
            app.dependency_overrides[get_async_redis] = previous


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
async def client(redis):
    """App wired to a working fake Redis."""
    async with _client_with(redis) as c:
        yield c


@pytest.fixture
async def broken_client():
    """App wired to a Redis whose every call fails at the connection level."""
    async with _client_with(BrokenRedis()) as c:
        yield c
