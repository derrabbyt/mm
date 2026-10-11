import datetime as dt
from collections.abc import Iterator

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
    return len(jsonld.events_in(html))


def parse(payload: RawPayload) -> Iterator[RawListing]:
    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        url = (node.get("url") or "").split("?")[0]
        if not url:
            continue
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        source_ref = tail.split("-", 1)[0] if tail[:1].isdigit() else tail
        if not source_ref or source_ref in seen:
            continue

        start: dt.datetime | dt.date | None = parse_iso_datetime(node.get("startDate"))
        if start is None:
            try:
                start = dt.date.fromisoformat(str(node.get("startDate"))[:10])
            except ValueError:
                continue
        seen.add(source_ref)

        where = jsonld.place(node)
        latitude, longitude = jsonld.coordinates(node)

        image = jsonld.image(node)

        artists = [
            one.get("name")
            for one in jsonld.as_list(node.get("performer"))
            if isinstance(one, dict) and one.get("name")
        ]

        yield RawListing(
            source_ref=source_ref,
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
            lat=latitude,
            lon=longitude,
            image_url=image,
            categories_raw=["Konzert"],
        )
