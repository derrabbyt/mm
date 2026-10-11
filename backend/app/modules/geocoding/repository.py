from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...core.exceptions import GeocodeCacheError
from .models import GeocodeCache
from .photon import Found


def cached(db: Session, key: str) -> GeocodeCache | None:
    try:
        return db.scalars(
            select(GeocodeCache).where(GeocodeCache.query_norm == key)
        ).one_or_none()
    except SQLAlchemyError as exc:
        raise GeocodeCacheError() from exc


def remember(
    db: Session, key: str, query: str, found: Found | None, provider: str
) -> None:
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
                    "miss_count": GeocodeCache.miss_count + (0 if found else 1),
                },
            )
        )
        db.commit()
    except SQLAlchemyError as exc:
        raise GeocodeCacheError() from exc
