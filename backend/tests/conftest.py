import uuid
from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from redis_fastapi import get_async_redis
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db.session import engine, get_db
from app.main import app
from app.models.account import Account
from app.models.meetup import Meetup
from app.services.accounts import get_current_account


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


@pytest.fixture
def account(db) -> Account:
    """A persisted account, since meetups and participants carry a real FK to
    one and would fail the constraint against a merely in-memory object."""
    account = Account(
        id=uuid.uuid4(),
        supabase_user_id=uuid.uuid4(),
        display_name="Ada",
    )
    db.add(account)
    db.commit()
    return account


@pytest.fixture
def meetup(db, account) -> Meetup:
    meetup = Meetup(
        id=uuid.uuid4(),
        created_by_account_id=account.id,
        name="Coffee",
        location="Vienna",
        starts_at=datetime.now(UTC),
    )
    db.add(meetup)
    db.commit()
    return meetup


@asynccontextmanager
async def _client_with(redis, db_override, account_override):
    """Build a client bound to `redis`/`db_override`, authenticated as
    `account_override`, restoring any previous overrides after (see
    save/restore rationale on the fixtures below)."""
    overrides = {
        get_async_redis: lambda: redis,
        get_db: lambda: db_override,
        # Stubbed so tests never reach Supabase for a JWKS key.
        get_current_account: lambda: account_override,
    }
    previous = {dep: app.dependency_overrides.get(dep) for dep in overrides}
    app.dependency_overrides.update(overrides)

    # raise_app_exceptions=False so the registered handlers produce a response
    # instead of the exception propagating into the test. Overriding the Redis
    # dependency also lets us skip the lifespan, which would dial a real server.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        for dep, prior in previous.items():
            if prior is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = prior


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
async def client(redis, db, account):
    """App wired to a working fake Redis and a real, rolled-back-after DB session."""
    async with _client_with(redis, db, account) as c:
        yield c


@pytest.fixture
async def broken_client(redis):
    """App wired to a working Redis but a DB whose every call fails.

    The account is a detached object rather than a row: resolving the caller
    normally hits the database, which would fail here before any route runs.
    """
    account = Account(id=uuid.uuid4(), supabase_user_id=uuid.uuid4())
    async with _client_with(redis, BrokenSession(), account) as c:
        yield c
