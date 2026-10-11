import hashlib
import os
import uuid
from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import fakeredis.aioredis
import psycopg2
import pytest
from httpx import ASGITransport, AsyncClient
from redis_fastapi import get_async_redis
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

TEST_DB_SUFFIX = "_test"


def _with_test_database(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"{parts.path}{TEST_DB_SUFFIX}"))


def _test_database_urls() -> tuple[str, str]:
    from app.core.config import Settings

    base = Settings()
    override = os.environ.get("TEST_DATABASE_URL")
    application = override or _with_test_database(base.app_database_url)
    scraper = _with_test_database(base.scraper_database_url)
    if not base.scraper_configured:
        scraper = application
    return application, scraper


def _ensure_database(url: str) -> None:
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    maintenance = urlunsplit(parts._replace(path="/postgres")).replace(
        "postgresql+psycopg2://", "postgresql://"
    )

    connection = psycopg2.connect(maintenance)
    try:
        connection.autocommit = True
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

os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
os.environ["SCRAPER_DATABASE_URL"] = _TEST_SCRAPER_DATABASE_URL

from app.core import config

config.settings = config.Settings()

_ensure_database(_TEST_DATABASE_URL)

from app.api.main import app
from app.db.session import engine, get_db
from app.modules.accounts.models import Account
from app.modules.accounts.service import get_current_account
from app.modules.meetups.models import Meetup

settings = config.settings

if settings.database_url != _TEST_DATABASE_URL:  # pragma: no cover - a guard
    raise RuntimeError(
        "the suite is pointed at "
        f"{settings.database_url.rsplit('/', 1)[-1]!r} rather than at its own "
        "database; refusing to run against the application's data"
    )


@pytest.fixture(autouse=True)
def archive_under_tmp(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "scrape_archive_dir", tmp_path / "archive", raising=True
    )


@pytest.fixture
def write_dataset(tmp_path):
    import h3
    import numpy as np

    from app.core import dataset as fmt
    from app.modules.matrix.service import describe_matrix

    def write(matrices=None, *, n_cells: int = 3, folder=None) -> Path:
        folder = folder or tmp_path / "vienna_vtest_h3r9"
        folder.mkdir(parents=True, exist_ok=True)
        overrides = dict(matrices or {})

        cells = [
            fmt.GridCell(
                id=index,
                lat=48.20 + index * 0.01,
                lon=16.37 + index * 0.01,
                h3=h3.latlng_to_cell(48.20 + index * 0.01, 16.37 + index * 0.01, 9),
            )
            for index in range(n_cells)
        ]
        grid = fmt.Grid(
            format_version=fmt.FORMAT_VERSION,
            grid_version="vtest_h3r9",
            grid_shape="hex",
            bbox_wgs84=fmt.BoundingBox(south=48.1, north=48.3, west=16.2, east=16.6),
            n_cells=n_cells,
            h3_res=9,
            cells=cells,
        )
        grid_json = grid.model_dump_json()
        (folder / "grid.json").write_text(grid_json)

        source = fmt.SourceFile(
            path="data/geodata/vienna.osm.pbf",
            sha256="0" * 64,
            sha256_short="0" * 8,
            size_bytes=1,
        )
        feed = source.model_copy(update={"sha256": "1" * 64})

        entries = {}
        for ordinal, key in enumerate(fmt.MATRIX_KEYS):
            mode = fmt.mode_of(key)
            transit = mode is fmt.MatrixMode.TRANSIT
            matrix = overrides.pop(key, None)
            if matrix is None:
                matrix = np.full(
                    (n_cells, n_cells), 60 * (ordinal + 1), dtype=np.uint16
                )
            np.save(folder / f"{key}.npy", matrix, allow_pickle=False)
            entries[key] = describe_matrix(
                file=f"{key}.npy",
                inputs=fmt.BuildInputs(
                    format_version=fmt.FORMAT_VERSION,
                    profile_version=fmt.PROFILE_VERSION,
                    engine="r5py",
                    engine_version="1.1.7",
                    grid_version="vtest_h3r9",
                    mode=mode,
                    max_seconds=fmt.MAX_SECONDS_PER_MODE[mode],
                    snap_to_network=True,
                    percentiles=fmt.PERCENTILES,
                    osm_sha256=source.sha256,
                    gtfs_sha256=feed.sha256 if transit else None,
                    departure=datetime(2026, 6, 10, 8, tzinfo=UTC) if transit else None,
                    departure_window_min=10 if transit else None,
                ),
                matrix=matrix,
                timezone="Europe/Vienna",
                wall_seconds=1.0,
            )
        if overrides:
            raise AssertionError(f"not Dataset matrices: {sorted(overrides)}")

        manifest = fmt.Manifest(
            format_version=fmt.FORMAT_VERSION,
            city="vienna",
            grid_version="vtest_h3r9",
            built_at=datetime(2026, 6, 15, tzinfo=UTC),
            grid=fmt.ManifestGrid(
                file="grid.json",
                sha256=hashlib.sha256(grid_json.encode()).hexdigest(),
                n_cells=n_cells,
                grid_shape="hex",
            ),
            sources={"osm": source, "gtfs": feed},
            matrix_conventions=fmt.CONVENTIONS,
            matrices=entries,
        )
        (folder / "manifest.json").write_text(manifest.model_dump_json(indent=2))
        return folder

    return write


class BrokenSession:
    def __getattr__(self, _name):
        def _fail(*args, **kwargs):
            raise OperationalError("<test>", {}, Exception("database is down"))

        return _fail


@pytest.fixture
def db() -> Generator[Session]:
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
    overrides = {
        get_async_redis: lambda: redis,
        get_db: lambda: db_override,
        get_current_account: lambda: account_override,
    }
    previous = {dep: app.dependency_overrides.get(dep) for dep in overrides}
    app.dependency_overrides.update(overrides)

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
    async with _client_with(redis, db, account) as c:
        yield c


@pytest.fixture
async def broken_client(redis):
    account = Account(id=uuid.uuid4(), supabase_user_id=uuid.uuid4())
    async with _client_with(redis, BrokenSession(), account) as c:
        yield c
