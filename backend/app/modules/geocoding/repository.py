"""Every SQLAlchemy statement the geocoding module runs.

`SQLAlchemyError` never leaves this file.

The cache is keyed on the normalised query rather than on a venue, because a
Listing carries its own address and the cache exists so that 650 Listings at 350
distinct addresses cost 350 lookups rather than 650. It outlives the Listings
that filled it, which is what makes a re-scrape cheap.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...core.exceptions import GeocodeCacheError
from .models import GeocodeCache
from .photon import Found


def cached(db: Session, key: str) -> GeocodeCache | None:
    """What the geocoder said last time, hit or miss."""
    try:
        return db.scalars(
            select(GeocodeCache).where(GeocodeCache.query_norm == key)
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise GeocodeCacheError() from exc


def remember(
    db: Session, key: str, query: str, found: Found | None, provider: str
) -> None:
    """Keep the answer, including a miss.

    A cached miss is worth as much as a cached hit: without it, every run asks
    again for the addresses that have already proved unresolvable, which is most
    of what a repeat run would otherwise spend its time on.
    """
    values = {
        "query_norm": key,
        "query_raw": query,
        "lat": found.latitude if found else None,
        "lon": found.longitude if found else None,
        "precision": found.precision if found else None,
        "provider": provider,
        "raw_response": found.raw if found else None,
        "geocoded_at": datetime.now(UTC),
        "miss_count": 0 if found else 1,
    }
    statement = insert(GeocodeCache).values(**values)
    try:
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[GeocodeCache.query_norm],
                set_={
                    **{
                        column: statement.excluded[column]
                        for column in values
                        if column not in {"query_norm", "miss_count"}
                    },
                    # Counts how often an address has failed, which is the
                    # ranking for deciding which are worth fixing by hand.
                    "miss_count": GeocodeCache.miss_count + (0 if found else 1),
                },
            )
        )
        db.commit()
    except SQLAlchemyError as exc:
        raise GeocodeCacheError() from exc
