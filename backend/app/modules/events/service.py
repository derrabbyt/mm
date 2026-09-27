"""Scraping the catalogue, and reading what is on near a point on a day.

Three steps, in order. A scrape fills the catalogue with Listings, one record
per Source. A rebuild groups each day's Listings into Events, so a happening
five Sources listed is one thing rather than five. The read serves those
Events.

The rebuild runs once, after every Source has been scraped, and not inside the
per-Source loop: a duplicate only becomes visible when *both* Sources have been
ingested, so nothing about grouping can be decided while one Source is being
processed.

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
from ...core.contracts import Address, Locate, Position
from ...core.http import HttpClient
from . import dedup, repository, sources
from .models import Event, Listing
from .normalize import normalize
from .schemas import EventRead
from .scraped import (
    VIENNA_TZ,
    BuiltEvent,
    NormalizedListing,
    RawListing,
    Rejected,
    SourceRunStats,
)
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


def _localised(lang_primary: str, de: str | None, en: str | None) -> str | None:
    """The side of a de/en pair that `lang_primary` names, falling back to the
    other because a handful of rows disagree with their own lang_primary."""
    return _first(en, de) if lang_primary == "en" else _first(de, en)


def _address(street: str | None, postcode: str | None, city: str | None) -> str | None:
    """`Stephansplatz 3, 1010 Wien`, or None when there is no street to build
    on - roughly 40% of geocoded Listings. Returning a bare "Wien" instead would
    look like a real answer and stop the caller from geocoding a better one."""
    street = _first(street)
    if street is None:
        return None

    locality = " ".join(part for part in (_first(postcode), _first(city)) if part)
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

    One entry per real-world happening, whichever Sources listed it - the
    grouping is a run's work, already done, and this only reads it.

    `day` must be the Venue's local calendar day, not the UTC one - Occurrences
    are dated by the local calendar.
    """
    rows = repository.find_events_near(
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
        title=_localised(event.lang_primary, event.title_de, event.title_en)
        or "Untitled event",
        starts_at=start_local,
        all_day=all_day,
        description=_localised(
            event.lang_primary, event.description_de, event.description_en
        ),
        # origin_url is null for about 70% of Listings, and an entry with no
        # link at all is worse than one pointing at the Source's own page.
        origin_url=_first(event.origin_url, event.url),
        image_url=_first(event.image_url),
        venue_name=_first(event.venue_name_raw),
        address=_address(event.street, event.postcode, event.city),
        position=Position(latitude=event.lat, longitude=event.lon),
        distance_meters=round(meters),
    )


def _candidate(
    listing: Listing, day: date, all_day: bool, completeness: int
) -> dedup.Candidate:
    """One Listing on one day, as the matcher sees it.

    `completeness` is not a matching signal - it decides which member of a
    group the Event reads as, so leaving it at zero makes that the Source's
    name in alphabetical order.
    """
    return dedup.Candidate(
        listing_id=listing.id,
        source=listing.source,
        date_local=day,
        title=_localised(listing.lang_primary, listing.title_de, listing.title_en)
        or "",
        title_norm=listing.title_norm,
        venue_name=listing.venue_name_raw,
        city=listing.city,
        position=_position(listing),
        all_day=all_day,
        completeness=completeness,
    )


def _content(listing: Listing, *, completeness: int) -> dedup.Content:
    """One Listing's share of an Event."""
    return dedup.Content(
        listing_id=listing.id,
        source=listing.source,
        lang_primary=listing.lang_primary,
        title_de=listing.title_de,
        title_en=listing.title_en,
        description_de=listing.description_de,
        description_en=listing.description_en,
        venue_name=listing.venue_name_raw,
        street=listing.street,
        postcode=listing.postcode,
        city=listing.city,
        position=_position(listing),
        url=listing.url,
        origin_url=listing.origin_url,
        image_url=listing.image_url,
        completeness=completeness,
    )


def _position(listing: Listing) -> Position | None:
    if listing.lat is None or listing.lon is None:
        return None
    return Position(latitude=listing.lat, longitude=listing.lon)


def _completeness(listing: Listing, all_day: bool, end_known: bool) -> int:
    """How much usable content a Listing carries, in the matcher's terms.

    The mapping from columns to what each one is worth lives here rather than
    in `dedup.py`: the weights are a matching rule, the columns are ours.
    """
    return dedup.completeness_score(
        positioned=listing.lat is not None and listing.lon is not None,
        street=bool(listing.street),
        described=bool(listing.description_de or listing.description_en),
        both_languages=bool(listing.title_de and listing.title_en),
        image=bool(listing.image_url),
        priced=listing.price_min is not None or bool(listing.is_free),
        ticket_url=bool(listing.ticket_url),
        all_day=all_day,
        end_known=end_known,
    )


def _built(
    group: dedup.Group,
    contents: dict[int, dedup.Content],
    durations: dict[int, int],
) -> BuiltEvent | None:
    """One group as a storable Event, or None if nobody could place it.

    A group no Source gave a position for is dropped rather than stored: the
    read filters on distance from a Rendezvous, so an Event with no position
    could never be returned to anyone. The Listings stay, and the run after the
    geocoder places one of them builds the Event.
    """
    primary = group.primary
    merged = dedup.gap_fill(
        contents[primary.listing_id],
        [
            contents[member.listing_id]
            for member in group.members
            if member.listing_id != primary.listing_id
        ],
    )
    if merged.position is None:
        return None

    return BuiltEvent(
        # The date is already a column of its own; the key only has to say
        # which Listing this group is named after.
        group_key=f"{merged.source}:{merged.listing_id}",
        date_local=group.date_local,
        duration_days=max(durations[member.listing_id] for member in group.members),
        primary_listing_id=merged.listing_id,
        listing_ids=[member.listing_id for member in group.members],
        source=merged.source,
        lang_primary=merged.lang_primary,
        title_de=merged.title_de,
        title_en=merged.title_en,
        description_de=merged.description_de,
        description_en=merged.description_en,
        venue_name_raw=merged.venue_name,
        street=merged.street,
        postcode=merged.postcode,
        city=merged.city,
        lat=merged.position.latitude,
        lon=merged.position.longitude,
        url=merged.url,
        origin_url=merged.origin_url,
        image_url=merged.image_url,
    )


def rebuild_day(db: Session, day: date, *, run_id: str) -> int:
    """Group one day's Listings into Events. Returns how many were written."""
    rows = repository.day_candidates(db, day)

    candidates: list[dedup.Candidate] = []
    contents: dict[int, dedup.Content] = {}
    durations: dict[int, int] = {}
    for listing, all_day, duration_days, end_known in rows:
        completeness = _completeness(listing, all_day, end_known)
        candidates.append(_candidate(listing, day, all_day, completeness))
        contents[listing.id] = _content(listing, completeness=completeness)
        durations[listing.id] = duration_days

    groups = dedup.group_day(candidates)
    built = [
        one
        for one in (_built(group, contents, durations) for group in groups)
        if one is not None
    ]
    return repository.write_events(db, day, built, run_id)


def rebuild_events(
    db: Session, *, run_id: str | None = None, today: date | None = None
) -> int:
    """Rebuild the Events of every day the catalogue still covers.

    Every day rather than only the days a run touched: a rule change has to
    reach the whole catalogue without a re-scrape, and a Listing retired by
    this run has to leave the Events of days nothing else changed. The pass is
    idempotent - the same Listings produce the same Events - so running it
    again is a no-op rather than a second set of rows.
    """
    run_id = run_id or new_run_id()
    today = today or datetime.now(ZoneInfo(VIENNA_TZ)).date()
    since = today - timedelta(days=settings.scrape_days_back)
    until = today + timedelta(days=settings.scrape_days_ahead)
    days = repository.days_to_rebuild(db, since=since, until=until)

    total = sum(rebuild_day(db, day, run_id=run_id) for day in days)
    # Whatever this run did not write is a group that no longer exists.
    dropped = repository.drop_events_not_built_by(db, run_id, since=since, until=until)
    logger.info(
        "rebuilt %d event(s) over %d day(s), dropped %d", total, len(days), dropped
    )
    return total


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


def _positioned(listing: NormalizedListing, locate: Locate | None) -> NormalizedListing:
    """The Listing with a position, if it needs one and its address allows it.

    Most Sources publish no coordinates at all, and a Listing without them cannot
    be found near a Rendezvous, because the read filters on distance. Geocoding
    is reached as a capability passed in rather than as an import: this layer
    holds the session, so it is not allowed to know the geocoding module exists.

    A Listing that already carries a position keeps it. The Source saw the
    happening; the geocoder is only guessing from words.
    """
    if locate is None or listing.lat is not None:
        return listing

    located = locate(
        Address(
            venue_name=listing.venue_name_raw,
            street=listing.street,
            postcode=listing.postcode,
            city=listing.city,
        )
    )
    if located is None:
        return listing
    return listing.model_copy(
        update={
            "lat": located.position.latitude,
            "lon": located.position.longitude,
            "geo_source": "geocoded",
            # What the geocoder actually worked out, not a flat guess: a house
            # number and a district are both "a position" and only one is worth
            # much to a query about what is nearby.
            "geo_precision": located.precision,
        }
    )


def scrape_source(
    db: Session,
    source: Source,
    *,
    run_id: str,
    today: date,
    locate: Locate | None = None,
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
    placed = 0
    for listing in storable:
        positioned = _positioned(listing, locate)
        if listing.lat is None and positioned.lat is not None:
            placed += 1
        repository.upsert_listing(db, positioned, run_id)
        occurrences += len(positioned.occurrences)

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
        "%s: parsed=%d stored=%d quarantined=%d occurrences=%d geocoded=%d "
        "retired=%d %s",
        spec.name,
        stats.parsed_count,
        stats.valid_count,
        stats.quarantined_count,
        stats.occurrences_count,
        placed,
        retired,
        "ok" if stats.fetched_ok else f"FAILED ({error})",
    )
    return stats


def scrape(
    db: Session, *, today: date | None = None, locate: Locate | None = None
) -> list[SourceRunStats]:
    """Scrape every Source, one after another.

    Sequential on purpose: twenty-one Sources at polite delays is still only
    minutes, it keeps the log readable, and it avoids being a burst of load on
    anyone. A Source that needs concurrency for detail pages does it inside its
    own `fetch`.

    `locate` resolves an address to a position. Without one, a Listing whose
    Source published no coordinates is stored anyway and simply cannot be found
    near a Rendezvous - the address is kept, so a later run can still place it.
    """
    run_id = new_run_id()
    discovered = sources.discover()
    logger.info("Run %s starting over %d source(s)", run_id, len(discovered))

    # Vienna's calendar day. The window a Source is asked for, and the window
    # normalisation accepts, are both local-calendar windows.
    today = today or datetime.now(ZoneInfo(VIENNA_TZ)).date()
    stats = [
        scrape_source(db, source, run_id=run_id, today=today, locate=locate)
        for source in discovered.values()
    ]

    # Last, and over every Source at once: two Sources describing the same
    # happening are only visible as one once both have been stored.
    rebuild_events(db, run_id=run_id, today=today)
    return stats
