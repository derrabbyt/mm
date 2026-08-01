from collections.abc import Generator
from contextlib import asynccontextmanager

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from redis_fastapi import get_async_redis
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.session import engine, get_db
from app.main import app


class BrokenSession:
    """A DB session where every operation fails, as during an outage."""

    def __getattr__(self, _name):
        def _fail(*args, **kwargs):
            raise OperationalError("<test>", {}, Exception("database is down"))

        return _fail


@pytest.fixture
def db() -> Generator[Session]:
    """A real session on a transaction that's rolled back after the test, so
    nothing a test commits survives it - see verify_rollback_isolation for why
    join_transaction_mode="create_savepoint" is what makes this safe to use
    even though the service layer calls session.commit() itself.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@asynccontextmanager
async def _client_with(redis, db_override):
    """Build a client bound to `redis`/`db_override`, restoring any previous
    overrides after (see save/restore rationale on the fixtures below)."""
    previous_redis = app.dependency_overrides.get(get_async_redis)
    previous_db = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_async_redis] = lambda: redis
    app.dependency_overrides[get_db] = lambda: db_override

    # raise_app_exceptions=False so the registered handlers produce a response
    # instead of the exception propagating into the test. Overriding the Redis
    # dependency also lets us skip the lifespan, which would dial a real server.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        for dep, previous in ((get_async_redis, previous_redis), (get_db, previous_db)):
            if previous is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = previous


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
async def client(redis, db):
    """App wired to a working fake Redis and a real, rolled-back-after DB session."""
    async with _client_with(redis, db) as c:
        yield c


@pytest.fixture
async def broken_client(redis):
    """App wired to a working Redis but a DB whose every call fails."""
    async with _client_with(redis, BrokenSession()) as c:
        yield c
