from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.schema import SchemaItem

# app.models is imported for the side effect of registering every model on
# Base.metadata. Without it autogenerate sees empty metadata and emits a DROP
# for each table.
from app import models  # noqa: F401
from app.core.config import settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# THIS IS SHIT AND NEEDS TO GO SOON WHEN WE MERGE IT FROM MICROSERVICE TO SHARED MODULAR MONOLITH

# Tables written by the activity-loader event scraper, which shares this database
# and manages its own schema. They are not in Base.metadata, so autogenerate would
# emit a DROP for every one of them. Keep in sync with that project's store.py.
SCRAPER_TABLES = frozenset(
    {
        "events",
        "occurrences",
        "cards",
        "dedup_members",
        "venue_links",
        "geocode_cache",
        "quarantine",
        "source_runs",
        "alert_state",
        "eventloader_meta",
    }
)


def include_object(
    object: SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Hide tables this project does not own from autogenerate.

    spatial_ref_sys is a real public-schema table installed by PostGIS, so pinning
    search_path (below) does not hide it from reflection. The scraper's tables are
    the same problem from a different direction: another writer owns them.
    """
    foreign = name == "spatial_ref_sys" or name in SCRAPER_TABLES
    return not (type_ == "table" and foreign)


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # The postgis/postgis image adds tiger/tiger_data/topology to search_path,
    # which makes their tables visible to reflection even though
    # include_schemas is off - pin it to public at the session level so
    # autogenerate only ever sees our own tables. Setting this via a statement
    # on the connection instead would open a transaction that never gets
    # committed, silently rolling back the whole migration on connection close.
    connectable = create_engine(
        settings.database_url,
        poolclass=pool.NullPool,
        connect_args={"options": "-c search_path=public"},
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
