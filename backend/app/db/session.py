from collections.abc import Generator
from typing import Annotated

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(engine, expire_on_commit=False)

# The scraping job connects as a role that owns `content` and has no grant on
# the application's tables, so a bug in scraping cannot reach an account or a
# meetup - see docs/adr/0001-content-schema.md. Same database, different role:
# a foreign key still has to be able to cross from `public` to `content`.
#
# Built unconditionally, because where no separate role is configured
# `scraper_database_url` is the application's own and this is simply a second
# pool onto it. `scrape_events` is what says which of the two it got.
scraper_engine = create_engine(settings.scraper_database_url, pool_pre_ping=True)

ScraperSessionLocal = sessionmaker(scraper_engine, expire_on_commit=False)


def get_db() -> Generator[Session]:
    with SessionLocal() as session:
        yield session


DbSessionDep = Annotated[Session, Depends(get_db)]
