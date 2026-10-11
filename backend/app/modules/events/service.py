import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ...core.config import settings
from ...core.contracts import Address, Locate, Position
from ...core.http import HttpClient
from . import archive, dedup, repository, sources
from .models import Event, Listing
from .normalize import normalize
from .schemas import EventRead
from .scraped import (
    VIENNA_TZ,
    BuiltEvent,
    NormalizedListing,
    RawListing,
    Rejected,
    ScrapeOutcome,
    SourceRunStats,
)
from .sources.spec import FetchContext, Source

logger = logging.getLogger(__name__)

DEFAULT_RADIUS_METERS = 1000
DEFAULT_LIMIT = 20


def _first(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def _localised(lang_primary: str, de: str | None, en: str | None) -> str | None:
    return _first(en, de) if lang_primary == "en" else _first(de, en)


def _address(street: str | None, postcode: str | None, city: str | None) -> str | None:
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
    run_id = run_id or new_run_id()
    today = today or datetime.now(ZoneInfo(VIENNA_TZ)).date()
    since = today - timedelta(days=settings.scrape_days_back)
    until = today + timedelta(days=settings.scrape_days_ahead)
    days = repository.days_to_rebuild(db, since=since, until=until)

    total = sum(rebuild_day(db, day, run_id=run_id) for day in days)
    dropped = repository.drop_events_not_built_by(db, run_id, since=since, until=until)
    logger.info(
        "rebuilt %d event(s) over %d day(s), dropped %d", total, len(days), dropped
    )
    return total


def new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


def _client_for(source: Source) -> HttpClient:
    spec = source.SPEC
    timeout = spec.timeout_seconds or settings.scrape_timeout_seconds
    return HttpClient(
        delay=spec.delay_seconds, timeout=timeout, use_cookies=spec.use_cookies
    )


def _collect(source: Source, ctx: FetchContext) -> tuple[list[RawListing], str | None]:
    name = source.SPEC.name
    try:
        payloads = list(source.fetch(ctx))
    except Exception as exc:
        logger.exception("Source %s could not be fetched", name)
        return [], f"{type(exc).__name__}: {exc}"

    listings: list[RawListing] = []

    for payload in payloads:
        archive.store(payload, source=name)
        try:
            listings.extend(source.parse(payload))
        except Exception:
            logger.exception("Source %s could not parse %s", name, payload.url)

    return listings, None


def _positioned(listing: NormalizedListing, locate: Locate | None) -> NormalizedListing:
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
            "geo_precision": located.precision,
        }
    )


def _retire_unless_collapsed(
    db: Session, source: str, run_id: str, *, since: date, until: date
) -> int:
    live, unseen = repository.listing_counts(
        db, source, run_id, since=since, until=until
    )
    share = unseen / live if live else 0.0
    if share > settings.scrape_max_retired_share:
        logger.error(
            "%s: refusing to retire %d of %d Listing(s) - %.0f%% is over the %.0f%% "
            "a run may retire, so the Source looks broken rather than empty",
            source,
            unseen,
            live,
            share * 100,
            settings.scrape_max_retired_share * 100,
        )
        return 0
    return repository.retire_unseen(db, source, run_id)


def scrape_source(
    db: Session,
    source: Source,
    *,
    run_id: str,
    today: date,
    locate: Locate | None = None,
) -> SourceRunStats:
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
        retired = _retire_unless_collapsed(
            db, spec.name, run_id, since=ctx.date_from, until=ctx.date_to
        )

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
) -> ScrapeOutcome:
    with repository.scrape_lock(db) as acquired:
        if not acquired:
            return ScrapeOutcome(declined="another run is already in progress")

        run_id = new_run_id()
        discovered = sources.discover()
        logger.info("Run %s starting over %d source(s)", run_id, len(discovered))

        today = today or datetime.now(ZoneInfo(VIENNA_TZ)).date()

        archive.prune(
            retention_days=settings.scrape_archive_retention_days, today=today
        )
        stats = [
            scrape_source(db, source, run_id=run_id, today=today, locate=locate)
            for source in discovered.values()
        ]

        rebuild_events(db, run_id=run_id, today=today)
        return ScrapeOutcome(sources=stats)
