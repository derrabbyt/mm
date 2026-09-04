"""Events happening near a point, on a day.

Reads the scraper's tables (see `models.py`); this project never writes them.
"""

from datetime import date, datetime

from sqlalchemy.orm import Session

from ...core.contracts import Position
from . import repository
from .models import Event
from .schemas import EventRead

DEFAULT_RADIUS_METERS = 1000
DEFAULT_LIMIT = 20


def _first(*candidates: str | None) -> str | None:
    """First candidate with something in it - the scraper writes blanks as
    often as NULLs, and an empty title is worse than a missing one."""
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _localised(event: Event, de: str | None, en: str | None) -> str | None:
    """The side of a de/en pair that `lang_primary` names, falling back to the
    other because a handful of rows disagree with their own lang_primary."""
    return _first(en, de) if event.lang_primary == "en" else _first(de, en)


def _address(event: Event) -> str | None:
    """`Stephansplatz 3, 1010 Wien`, or None when there is no street to build
    on - roughly 40% of geocoded events. Returning a bare "Wien" instead would
    look like a real answer and stop the caller from geocoding a better one."""
    street = _first(event.street)
    if street is None:
        return None

    locality = " ".join(
        part for part in (_first(event.postcode), _first(event.city)) if part
    )
    return f"{street}, {locality}" if locality else street


def get_events_near(
    db: Session,
    position: Position,
    day: date,
    not_before: datetime,
    radius_meters: int = DEFAULT_RADIUS_METERS,
    limit: int = DEFAULT_LIMIT,
) -> list[EventRead]:
    """Events showing on `day` within `radius_meters` of `position`, nearest first.

    `day` must be the venue's local calendar day, not the UTC one - the scraper
    dates occurrences by the local calendar.
    """
    rows = repository.find_near(
        db,
        latitude=position.latitude,
        longitude=position.longitude,
        day=day,
        not_before=not_before,
        radius_meters=radius_meters,
        limit=limit,
    )

    return [
        _to_schema(event, start_local, all_day, meters)
        for event, start_local, all_day, meters in rows
    ]


def _to_schema(
    event: Event, start_local: datetime, all_day: bool, meters: float
) -> EventRead:
    return EventRead(
        id=event.id,
        title=_localised(event, event.title_de, event.title_en) or "Untitled event",
        starts_at=start_local,
        all_day=all_day,
        description=_localised(event, event.description_de, event.description_en),
        # origin_url is null for about 70% of events, and a card with no link at
        # all is worse than one pointing at the aggregator's page.
        origin_url=_first(event.origin_url, event.url),
        image_url=_first(event.image_url),
        venue_name=_first(event.venue_name_raw),
        address=_address(event),
        position=Position(latitude=event.lat, longitude=event.lon),
        distance_meters=round(meters),
    )
