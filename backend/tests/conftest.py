"""Shared fixtures, and the database the suite runs against.

The redirection below happens before any application module is imported, which
is why it is written as statements at the top of this file rather than as a
fixture. `app.db.session` builds its engines from the settings at import time, so
by the time a fixture could run, the process is already pointed at whatever the
environment said - and for a suite that writes Listings and runs the scrape job,
that has to be a database of its own. See tests/test_isolation.py.
"""

import os
import uuid
from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import fakeredis.aioredis
import psycopg2
import pytest
from httpx import ASGITransport, AsyncClient
from redis_fastapi import get_async_redis
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

# --- the suite's own database, chosen before the application is imported -----

#: Suffix for the test database, so its name says what it is at a glance in
#: `\l` and in any log line that quotes a DSN.
TEST_DB_SUFFIX = "_test"


def _with_test_database(url: str) -> str:
    """The same DSN - same host, same role, same password - other database."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"{parts.path}{TEST_DB_SUFFIX}"))


def _test_database_urls() -> tuple[str, str]:
    """Where the suite connects, as the application and as the scraping job.

    Two of them, and only the database name differs from the real pair. The
    scraping job's role is the point of `test_scraper_role.py` - swapping it for
    the application's would have those tests connect as a superuser, which can
    do everything they assert it cannot, so they would fail loudly or, worse,
    pass having proven nothing.
    """
    from app.core.config import Settings

    base = Settings()
    override = os.environ.get("TEST_DATABASE_URL")
    application = override or _with_test_database(base.app_database_url)
    scraper = _with_test_database(base.scraper_database_url)
    # Unconfigured, the job shares the application's connection; keep that true.
    if not base.scraper_configured:
        scraper = application
    return application, scraper


def _ensure_database(url: str) -> None:
    """Create the database if it is not there yet, and migrate it to head.

    Done here rather than in a README step so that `pytest` is the only command
    anyone has to know, and so a schema change cannot leave the suite running
    against yesterday's tables.
    """
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    maintenance = urlunsplit(parts._replace(path="/postgres")).replace(
        "postgresql+psycopg2://", "postgresql://"
    )

    connection = psycopg2.connect(maintenance)
    try:
        connection.autocommit = True  # CREATE DATABASE cannot run in one
        with connection.cursor() as cursor:
            cursor.execute("select 1 from pg_database where datname = %s", (name,))
            if cursor.fetchone() is None:
                cursor.execute(f'create database "{name}"')
    finally:
        connection.close()

    # PostGIS, which the image installs only into POSTGRES_DB, so a database
    # created here does not have it. The read path measures distances with
    # ST_DistanceSphere, so without this every events query fails on a missing
    # function rather than on anything a test is about.
    connection = psycopg2.connect(
        url.replace("postgresql+psycopg2://", "postgresql://")
    )
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("create extension if not exists postgis")
    finally:
        connection.close()

    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


_TEST_DATABASE_URL, _TEST_SCRAPER_DATABASE_URL = _test_database_urls()


def _database_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def _refuse_the_application_database() -> None:
    """Stop before collection if the suite would run on the real database.

    A failing assertion is not enough. By the time one runs, earlier tests have
    already written - the suite stores Listings and runs the scrape job - so the
    check has to happen while nothing has connected yet. This is the one that
    makes "the tests never touch the application's data" a fact rather than an
    intention.
    """
    from app.core.config import Settings

    application = _database_name(Settings().app_database_url)
    if _database_name(_TEST_DATABASE_URL) == application:
        raise RuntimeError(
            f"the suite is pointed at {application!r}, which is the "
            "application's own database. Unset TEST_DATABASE_URL, or point it "
            f"at something other than {application!r}: these tests write "
            "Listings and run the scrape job."
        )


_refuse_the_application_database()

# Both connections, because a test that runs the scrape job opens the scraper's.
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
os.environ["SCRAPER_DATABASE_URL"] = _TEST_SCRAPER_DATABASE_URL

# Deriving the name above had to read the settings, which means the module-level
# instance already exists and already read an environment that did not say any
# of this. Rebuilt here, before anything that holds an engine is imported, so
# that `app.db.session` binds a Settings that knows where the suite connects.
from app.core import config

config.settings = config.Settings()

_ensure_database(_TEST_DATABASE_URL)

from app.api.main import app
from app.db.session import engine, get_db
from app.modules.accounts.models import Account
from app.modules.accounts.service import get_current_account
from app.modules.meetups.models import Meetup

# The rebuilt instance, not a second import of the name that was bound earlier.
settings = config.settings

if settings.database_url != _TEST_DATABASE_URL:  # pragma: no cover - a guard
    raise RuntimeError(
        "the suite is pointed at "
        f"{settings.database_url.rsplit('/', 1)[-1]!r} rather than at its own "
        "database; refusing to run against the application's data"
    )


@pytest.fixture(autouse=True)
def archive_under_tmp(tmp_path, monkeypatch) -> None:
    """No test writes into the real payload archive.

    Autouse rather than opt-in: a scrape archives every payload it fetches, so
    any test that runs the job would otherwise leave documents in the directory
    a deployment keeps its evidence in.
    """
    monkeypatch.setattr(
        settings, "scrape_archive_dir", tmp_path / "archive", raising=True
    )


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
