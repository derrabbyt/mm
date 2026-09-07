"""What is on near a point, on a day.

One record per Source, so a happening two Sources listed comes back twice - a
Listing is not yet an Event. Deduplicating them is a later step.
"""

from datetime import date, datetime

from sqlalchemy.orm import Session

from ...core.contracts import Position
from . import repository
from .models import Listing
from .schemas import EventRead

DEFAULT_RADIUS_METERS = 1000
DEFAULT_LIMIT = 20


def _first(*candidates: str | None) -> str | None:
    """First candidate with something in it - Sources yield blanks as often as
    NULLs, and an empty title is worse than a missing one."""
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _localised(listing: Listing, de: str | None, en: str | None) -> str | None:
    """The side of a de/en pair that `lang_primary` names, falling back to the
    other because a handful of rows disagree with their own lang_primary."""
    return _first(en, de) if listing.lang_primary == "en" else _first(de, en)


def _address(listing: Listing) -> str | None:
    """`Stephansplatz 3, 1010 Wien`, or None when there is no street to build
    on - roughly 40% of geocoded Listings. Returning a bare "Wien" instead would
    look like a real answer and stop the caller from geocoding a better one."""
    street = _first(listing.street)
    if street is None:
        return None

    locality = " ".join(
        part for part in (_first(listing.postcode), _first(listing.city)) if part
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
    """What is on for `day` within `radius_meters` of `position`, nearest first.

    `day` must be the Venue's local calendar day, not the UTC one - Occurrences
    are dated by the local calendar.
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
        _to_schema(listing, start_local, all_day, meters)
        for listing, start_local, all_day, meters in rows
    ]


def _to_schema(
    listing: Listing, start_local: datetime, all_day: bool, meters: float
) -> EventRead:
    return EventRead(
        id=listing.id,
        title=_localised(listing, listing.title_de, listing.title_en)
        or "Untitled event",
        starts_at=start_local,
        all_day=all_day,
        description=_localised(listing, listing.description_de, listing.description_en),
        # origin_url is null for about 70% of Listings, and an entry with no
        # link at all is worse than one pointing at the Source's own page.
        origin_url=_first(listing.origin_url, listing.url),
        image_url=_first(listing.image_url),
        venue_name=_first(listing.venue_name_raw),
        address=_address(listing),
        position=Position(latitude=listing.lat, longitude=listing.lon),
        distance_meters=round(meters),
    )
