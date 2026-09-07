"""Scraping the catalogue, and reading what is on near a point on a day.

The read answers with one record per Source, so a happening two Sources listed
comes back twice - a Listing is not yet an Event. Deduplicating them is a later
step.

The scrape is orchestration only. Everything it decides is decided elsewhere: a
Source knows how to fetch and parse itself, `normalize.py` decides what is
storable, and `repository.py` owns the SQL. What is left here is the order of
those steps, and the two rules about when a step may run at all - a run that
could not fetch never retires anything, and one Source failing never stops the
others.
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.contracts import Position
from . import repository, sources
from .models import Listing
from .normalize import normalize
from .schemas import EventRead
from .scraped import (
    VIENNA_TZ,
    NormalizedListing,
    RawListing,
    Rejected,
    SourceRunStats,
)
from .sources.http import HttpClient
from .sources.spec import FetchContext, Source

logger = logging.getLogger(__name__)

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


def new_run_id() -> str:
    """A name for one scrape, stamped so runs sort chronologically."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def _client_for(source: Source) -> HttpClient:
    spec = source.SPEC
    timeout = spec.timeout_seconds or settings.scrape_timeout_seconds
    return HttpClient(
        delay=spec.delay_seconds, timeout=timeout, use_cookies=spec.use_cookies
    )


def _collect(source: Source, ctx: FetchContext) -> tuple[list[RawListing], str | None]:
    """Fetch and parse one Source. Returns what it yielded and what went wrong.

    A fetch that raises is reported, not re-raised: one site being down must not
    cost the other twenty their run. A single payload that will not parse is
    narrower still - it costs that document and nothing else.
    """
    name = source.SPEC.name
    try:
        payloads = list(source.fetch(ctx))
    except Exception as exc:
        logger.exception("Source %s could not be fetched", name)
        return [], f"{type(exc).__name__}: {exc}"

    listings: list[RawListing] = []

    for payload in payloads:
        try:
            listings.extend(source.parse(payload))
        except Exception:
            logger.exception("Source %s could not parse %s", name, payload.url)

    return listings, None


def scrape_source(
    db: Session, source: Source, *, run_id: str, today: date
) -> SourceRunStats:
    """Fetch, parse, normalise and store one Source. Never raises for its own
    failures - they are recorded against the run instead."""
    spec = source.SPEC
    started = datetime.now(UTC)

    http = _client_for(source)
    ctx = FetchContext(
        http=http,
        date_from=today - timedelta(days=settings.scrape_days_back),
        date_to=today + timedelta(days=settings.scrape_days_ahead),
        locales=spec.locales,
    )

    raw, error = _collect(source, ctx)

    storable: list[NormalizedListing] = []
    refused: list[Rejected] = []
    for one in raw:
        result = normalize(one, source=spec.name, today=today)
        if isinstance(result, Rejected):
            refused.append(result)
        else:
            storable.append(result)

    # A write that fails is not survivable the way a fetch that fails is: it
    # means the database, not the site, so it stops the run rather than being
    # recorded and stepped over.
    occurrences = 0
    for listing in storable:
        repository.upsert_listing(db, listing, run_id)
        occurrences += len(listing.occurrences)

    # Only a run that actually fetched something may conclude that what it did
    # not see is gone. Without the `storable` guard, a site answering 200 with
    # an empty page would retire the whole Source.
    retired = 0
    if error is None and storable:
        retired = repository.retire_unseen(db, spec.name, run_id)

    quarantined = repository.quarantine(db, refused, run_id)

    finished = datetime.now(UTC)
    stats = SourceRunStats(
        run_id=run_id,
        source=spec.name,
        started_at=started,
        finished_at=finished,
        duration_ms=int((finished - started).total_seconds() * 1000),
        fetched_ok=error is None,
        requests=http.request_count,
        raw_bytes=http.bytes_fetched,
        http_status_counts={str(k): v for k, v in http.status_counts.items()},
        parsed_count=len(raw),
        valid_count=len(storable),
        quarantined_count=quarantined,
        occurrences_count=occurrences,
        error=error,
    )
    # Commits this Source's transaction - see the repository.
    repository.record_source_run(db, stats)

    logger.info(
        "%s: parsed=%d stored=%d quarantined=%d occurrences=%d retired=%d %s",
        spec.name,
        stats.parsed_count,
        stats.valid_count,
        stats.quarantined_count,
        stats.occurrences_count,
        retired,
        "ok" if stats.fetched_ok else f"FAILED ({error})",
    )
    return stats


def scrape(db: Session, *, today: date | None = None) -> list[SourceRunStats]:
    """Scrape every Source, one after another.

    Sequential on purpose: twenty-one Sources at polite delays is still only
    minutes, it keeps the log readable, and it avoids being a burst of load on
    anyone. A Source that needs concurrency for detail pages does it inside its
    own `fetch`.
    """
    run_id = new_run_id()
    discovered = sources.discover()
    logger.info("Run %s starting over %d source(s)", run_id, len(discovered))

    # Vienna's calendar day. The window a Source is asked for, and the window
    # normalisation accepts, are both local-calendar windows.
    today = today or datetime.now(ZoneInfo(VIENNA_TZ)).date()
    return [
        scrape_source(db, source, run_id=run_id, today=today)
        for source in discovered.values()
    ]
