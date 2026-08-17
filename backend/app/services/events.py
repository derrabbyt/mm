"""Events happening near a meetup's rendezvous point.

Reads the scraper's tables (see `models/event.py`); this project never writes
them.
"""

import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, Float, func, or_, select, true
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..core.exceptions import EventsLoadError
from ..models.event import Event, Occurrence
from ..schemas.event import EventRead
from ..schemas.position import Position
from .meetups import get_owned_meetup
from .travel_times import get_rendezvous, to_local

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


def _distance_meters(latitude: float, longitude: float) -> ColumnElement[float]:
    """Great-circle metres from every event row to this point. The events table
    stores plain lat/lon columns, so there is no geometry index to hit; at a few
    thousand rows the sequential scan is not worth a schema we do not own."""
    return func.ST_DistanceSphere(
        func.ST_MakePoint(Event.lon, Event.lat),
        func.ST_MakePoint(longitude, latitude),
    ).cast(Float)


def get_rendezvous_events(
    db: Session,
    meetup_id: str,
    account_id: uuid.UUID,
    radius_meters: int = DEFAULT_RADIUS_METERS,
    limit: int = DEFAULT_LIMIT,
) -> list[EventRead]:
    """Events on the day of the meetup, within `radius_meters` of where its
    participants should gather, nearest first.

    The rendezvous is recomputed rather than passed in, so the events are
    guaranteed to belong to the point the caller was actually given. Both calls
    below re-check ownership; the second one is served from the session's
    identity map.
    """
    meetup = get_owned_meetup(db, meetup_id, account_id)
    rendezvous = get_rendezvous(db, meetup_id, account_id)

    # The scraper dates occurrences by the venue's local calendar, so compare
    # against the meetup's local day rather than its UTC one - a 22:35 meetup in
    # Vienna is already the next day in UTC.
    meetup_day = to_local(meetup.starts_at).date()

    distance = _distance_meters(
        rendezvous.position.latitude, rendezvous.position.longitude
    )

    # An event runs on many dates; LATERAL picks the one showing that matters
    # here - the next one that day - and drops events with nothing on at all.
    next_occurrence = (
        select(Occurrence.start_local, Occurrence.all_day)
        .where(
            Occurrence.event_id == Event.id,
            Occurrence.date_local == meetup_day,
            # An exhibition open all day is still worth showing at 20:00; a
            # concert that started at 18:00 is not. All-day rows carry a
            # midnight start that would otherwise fail this test every time.
            or_(Occurrence.all_day, Occurrence.start_utc >= meetup.starts_at),
        )
        .order_by(Occurrence.start_utc)
        .limit(1)
        .lateral("next_occurrence")
    )

    query = (
        select(
            Event,
            next_occurrence.c.start_local,
            next_occurrence.c.all_day,
            distance.label("distance_meters"),
        )
        .join(next_occurrence, true())
        .where(
            Event.disappeared_at.is_(None),
            Event.lat.is_not(None),
            Event.lon.is_not(None),
            distance <= radius_meters,
        )
        .order_by(distance, next_occurrence.c.start_local)
        .limit(limit)
    )

    try:
        rows = db.execute(query).all()
    except SQLAlchemyError as exc:
        raise EventsLoadError() from exc

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
