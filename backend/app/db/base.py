from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Named consistently so Alembic autogenerate can diff constraints without
# manual renames.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# Bulk-ingested content - scraped listings and everything derived from them -
# lives apart from the application's own tables, so the scraping job can connect
# as a role with no write grant on `public`. See docs/adr/0001-content-schema.md.
CONTENT_SCHEMA = "content"
