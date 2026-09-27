"""austria.info - the national tourism board's Algolia index.

The site is a Nuxt SPA, but its Algolia credentials sit in the page source, so the
index can be queried directly with no auth beyond the public search key.

Two traps, both of which produce badly wrong counts:

* **Every happening is stored once per locale** (~10 copies: `de-at`, `de-ch`,
  `de-de`, `en-gb`, `en-us`, `it`, ...). Without a `language` facet filter you get
  tenfold duplicates. `story_id` is the cross-locale identity, so it is what
  identifies the Listing.
* **`locationCity:Vienna` returns zero.** The city field is unreliable; the
  usable geo key is the `state` facet (`state:Vienna`).

Low volume for Vienna (~13 on a given day) and skewed to long-running
institutions rather than dated happenings. Carried for completeness; it is not a
coverage workhorse.
"""

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
            # Overlap test on the numeric timestamps, so multi-day runs that
            # straddle the window are included.
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
        # story_id is stable across locales; objectID is not.
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

        # The index carries dates as "YYYY-MM-DD HH:MM" but the time is 00:00
        # throughout, so these are day-level, not clock-level.
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
