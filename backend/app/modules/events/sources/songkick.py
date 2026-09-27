"""songkick - live music, with a native date filter and full geo.

Date filtering works via `filters[minDate]` / `filters[maxDate]`, which take
**US `MM/DD/YYYY`** - an ISO date is silently ignored. 50 JSON-LD `MusicEvent`
blocks per page, each with a `PostalAddress` and `GeoCoordinates`, so these
Listings need no geocoding.

**Rate limiting looks like an unsupported parameter.** Rapid requests answer
`406`, which reads as "this filter isn't supported"; at ~8s spacing every request
succeeded. Hence the unusually large delay, on top of the shared client's retry
with backoff.
"""

import datetime as dt
from collections.abc import Iterator
from typing import Any

from ....core.http import encode_query
from ..scraped import RawListing, RawOccurrence
from . import jsonld
from .dates import parse_iso_datetime
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="songkick",
    locales=("en",),
    delay_seconds=8.0,
    notes=(
        "filters[minDate]/[maxDate] need US MM/DD/YYYY; ISO is ignored. "
        "406 means THROTTLED, not 'unsupported parameter': ~8s spacing works. "
        "50 MusicEvents/page with geo."
    ),
)

BASE = "https://www.songkick.com"
# Vienna metro area id, from the site's own URL.
METRO = "26771-austria-vienna"
MAX_PAGES = 8


def _us_date(value: dt.date) -> str:
    return f"{value.month:02d}/{value.day:02d}/{value.year}"


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    for page in range(1, MAX_PAGES + 1):
        params = [
            ("filters[minDate]", _us_date(ctx.date_from)),
            ("filters[maxDate]", _us_date(ctx.date_to)),
        ]
        if page > 1:
            params.append(("page", str(page)))
        resp = ctx.http.get(f"{BASE}/metro-areas/{METRO}?{encode_query(params)}")
        yield RawPayload(
            url=resp.url, body=resp.body, kind="listing", meta={"page": page}
        )
        if _count_results(resp.text) < 50:
            break


def _count_results(html: str) -> int:
    """Named as eventfinder's is, for the same "was this page full?" job."""
    return len(jsonld.events_in(html))


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse(payload: RawPayload) -> Iterator[RawListing]:
    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        url = (node.get("url") or "").split("?")[0]
        if not url:
            continue
        # .../id/43198223-kultursommer-wien -> the numeric id is stable.
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        source_event_id = tail.split("-", 1)[0] if tail[:1].isdigit() else tail
        if not source_event_id or source_event_id in seen:
            continue

        start: dt.datetime | dt.date | None = parse_iso_datetime(node.get("startDate"))
        if start is None:
            # A date with no time at all is still usable; it means all-day.
            try:
                start = dt.date.fromisoformat(str(node.get("startDate"))[:10])
            except ValueError:
                continue
        seen.add(source_event_id)

        where = jsonld.place(node)
        geo = jsonld.mapping(jsonld.mapping(node.get("location")).get("geo"))

        # Protocol-relative in the markup; normalize prefixes the scheme. Usually
        # the headline artist's photo rather than event artwork - for a gig
        # listing that is the picture the site itself shows.
        image = jsonld.first(node.get("image"))

        artists = [
            one.get("name")
            for one in jsonld.as_list(node.get("performer"))
            if isinstance(one, dict) and one.get("name")
        ]

        yield RawListing(
            source_event_id=source_event_id,
            occurrences=[RawOccurrence(start=start)],
            url=url,
            title=node.get("name") or (artists[0] if artists else None),
            description=", ".join(artists) if artists else None,
            lang="en",
            venue_name=where["venue_name"],
            street=where["street"],
            postcode=where["postcode"],
            city=where["city"] or "Wien",
            country=where["country"] or "AT",
            lat=_float(geo.get("latitude")),
            lon=_float(geo.get("longitude")),
            image_url=image if isinstance(image, str) else None,
            # Everything here is live music by definition; the site has no
            # per-event category of its own.
            categories_raw=["Konzert"],
        )
