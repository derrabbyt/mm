from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.schema import SchemaItem

# app.metadata is imported for the side effect of registering every model on
# Base.metadata. Without it autogenerate sees empty metadata and emits a DROP
# for each table.
from app import metadata  # noqa: F401
from app.core.config import settings
from app.db.base import CONTENT_SCHEMA, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# The schemas this project owns. `None` is how Alembic names the connection's
# default schema, which is `public` here; PostGIS's own tiger, tiger_data and
# topology schemas are on the search path but are not ours to migrate.
OWNED_SCHEMAS = frozenset({None, "public", CONTENT_SCHEMA})


def include_name(name: str | None, type_: str, parent_names: dict[str, Any]) -> bool:
    """Restrict autogenerate to the schemas this project owns.

    `include_schemas` makes Alembic reflect every schema in the database rather
    than just the default one, which is what lets it see `content` - and also
    what would otherwise let it see PostGIS's.
    """
    if type_ == "schema":
        return name in OWNED_SCHEMAS
    return True


def include_object(
    object: SchemaItem, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Hide PostGIS's own table from autogenerate.

    `spatial_ref_sys` is a real table in `public`, installed by the extension,
    so filtering by schema does not reach it.
    """
    return not (type_ == "table" and name == "spatial_ref_sys")


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # search_path stays pinned to `public` alone, and `content` is reached by
    # `include_schemas` instead. Reflection of the default schema returns
    # whatever is *visible* on the search path, so a schema named here is
    # reflected twice - once unqualified and once under its own name - and
    # autogenerate reads the unqualified copy as a table to drop. That is also
    # why the postgis image's tiger/tiger_data/topology must stay off it.
    #
    # Setting this with a statement on the connection instead would open a
    # transaction that never gets committed, silently rolling back the whole
    # migration on connection close.
    connectable = create_engine(
        settings.database_url,
        poolclass=pool.NullPool,
        connect_args={"options": "-c search_path=public"},
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
