import datetime as dt
import json
from collections.abc import Iterator
from typing import Any

from ..scraped import RawListing, RawOccurrence
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="austria_info",
    locales=("en",),
    delay_seconds=0.5,
    notes=(
        "The Algolia index stores each one once PER LOCALE (~10 copies), so the "
        "language facet filter is mandatory. locationCity:Vienna returns 0; use "
        "the state facet. Mostly long-running institutions, not dated ones."
    ),
)

APP_ID = "EQKYWCEXMH"
# Public search-only key, as published in the site's own page source.
API_KEY = "e2fd21a0e7c5c6c392255c354c143a07"
INDEX = "oew-b2c-events"
ENDPOINT = f"https://{APP_ID}-dsn.algolia.net/1/indexes/{INDEX}/query"

HITS_PER_PAGE = 100
MAX_PAGES = 10
LOCALE = "en-gb"


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    start = dt.datetime.combine(ctx.date_from, dt.time.min, tzinfo=dt.UTC)
    end = dt.datetime.combine(ctx.date_to, dt.time.max, tzinfo=dt.UTC)

    for page in range(MAX_PAGES):
        payload = {
            "query": "",
            "hitsPerPage": HITS_PER_PAGE,
            "page": page,
            "filters": (
                f"dateFromTimestamp <= {int(end.timestamp())} "
                f"AND dateToTimestamp >= {int(start.timestamp())}"
            ),
            "facetFilters": [["state:Vienna"], [f"language:{LOCALE}"]],
        }
        resp = ctx.http.post_json(
            ENDPOINT,
            payload,
            headers={
                "X-Algolia-Application-Id": APP_ID,
                "X-Algolia-API-Key": API_KEY,
            },
        )
        yield RawPayload(
            url=resp.url, body=resp.body, kind="query", meta={"page": page}
        )

        try:
            data = resp.json()
        except ValueError:
            return
        if page + 1 >= int(data.get("nbPages") or 0):
            return


def _link(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("url") or value.get("cached_url")
    return value if isinstance(value, str) else None


def parse(payload: RawPayload) -> Iterator[RawListing]:
    data = json.loads(payload.text)
    if data.get("message") and not data.get("hits"):
        return

    for hit in data.get("hits") or []:
        listing_id = hit.get("story_id") or hit.get("objectID")
        if not listing_id:
            continue

        start_raw = hit.get("dateFrom")
        if not start_raw:
            continue
        try:
            start = dt.date.fromisoformat(str(start_raw)[:10])
            end_raw = hit.get("dateTo") or start_raw
            end = dt.date.fromisoformat(str(end_raw)[:10])
        except ValueError:
            continue

        occurrence = RawOccurrence(start=start, end=end if end != start else None)

        categories = [c for c in (hit.get("category") or []) if c]
        categories += [t for t in (hit.get("topics") or []) if t]

        image = hit.get("image")
        if isinstance(image, dict):
            image = image.get("cdn") or image.get("url")

        slug = hit.get("fullSlug")
        yield RawListing(
            source_ref=str(listing_id),
            occurrences=[occurrence],
            url=f"https://www.austria.info/{slug}" if slug else None,
            origin_url=_link(hit.get("eventLink")),
            title=hit.get("headline"),
            description=hit.get("text"),
            lang="en",
            venue_name=hit.get("locationVenue"),
            city=hit.get("locationCity") or "Wien",
            country="AT",
            categories_raw=categories,
            image_url=image if isinstance(image, str) else None,
        )
