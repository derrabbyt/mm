"""ohschonhell - a community-submitted electronic music calendar.

Fully client-rendered; the endpoints live in
`/wp-content/themes/event4/js/event.js`. WordPress's own `/wp-json/` is
disabled.

**`osh_page` is mandatory.** Omit it and the `.at` domain serves **Hamburg**
data - the backend is shared across ohschonhell.de's city subdomains. Cities
seen: `wien`, `hamburg`, `berlin`, `koeln`, `stuttgart`, `capetown`,
`rheinmain`.

Returns a whole month at a time (`months[].month_days[].events[]`), so the
requested window is left to normalisation.

Coverage is thin and near-term: July 99 events, August 17, September 0 at the
time of research. **Empty days are genuine**, not breakage - which is why a
run's per-Source statistics are the right place to notice a Source going quiet,
rather than a fixed threshold here.
"""

import datetime as dt
import json
import re
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup

from ..scraped import RawListing, RawOccurrence
from .dates import combine, parse_iso_when, parse_time
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="ohschonhell",
    locales=("de",),
    delay_seconds=0.5,
    notes=(
        "osh_page=wien is MANDATORY - without it the .at domain serves Hamburg "
        "data. Returns whole months; filter the window locally. An event "
        "appears in both `days` and `months[].month_days`, so deduplicate. "
        "Thin coverage; empty days are genuine, not breakage."
    ),
)

ENDPOINT = "https://ohschonhell.at/ajax/data_load.php"
BASE = "https://ohschonhell.at"
CITY = "wien"


def months_in(date_from: dt.date, date_to: dt.date) -> list[str]:
    """Every `YYYY-MM` the window touches, because a request takes a month."""
    found: list[str] = []
    year, month = date_from.year, date_from.month
    while (year, month) <= (date_to.year, date_to.month):
        found.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return found


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    for month in months_in(ctx.date_from, ctx.date_to):
        resp = ctx.http.post_form(
            ENDPOINT,
            {"month": month, "osh_page": CITY},
            headers={"Referer": f"{BASE}/"},
        )
        yield RawPayload(
            url=resp.url, body=resp.body, kind="month", meta={"month": month}
        )


def _lineup(value: Any) -> str | None:
    """Flatten the `lineup` blob, which is HTML: SoundCloud embeds, <br> lists.

    Truncated, because it can run to whole press releases and it is being
    stored as a description rather than as a line-up.
    """
    if not value:
        return None
    text = BeautifulSoup(str(value), "lxml").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)[:400] or None


def _coordinate(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # The feed writes 0 for "not placed", and (0, 0) is in the Gulf of Guinea.
    return number if number else None


def parse(payload: RawPayload) -> Iterator[RawListing]:
    data = json.loads(payload.text)
    seen: set[str] = set()

    # `days` (the initial view) and `months` can both be present, and an event
    # appears in each, so both are walked and the result deduplicated.
    buckets: list[dict] = list(data.get("days") or [])
    for month in data.get("months") or []:
        buckets.extend(month.get("month_days") or [])

    for bucket in buckets:
        for item in bucket.get("events") or []:
            source_ref = str(item.get("event_id") or "")
            if not source_ref or source_ref in seen:
                continue

            day = parse_iso_when(item.get("date"))
            if day is None:
                continue
            seen.add(source_ref)

            start = combine(day, parse_time(str(item.get("event_time") or "")))

            post = item.get("event_post") or ""
            yield RawListing(
                source_ref=source_ref,
                occurrences=[RawOccurrence(start=start)],
                url=f"{BASE}{post}" if post.startswith("/") else (post or None),
                title=item.get("name"),
                description=_lineup(item.get("lineup")),
                lang="de",
                venue_name=item.get("location_name"),
                # location_city is free text: Vienna / Wien / VIENNA, or a
                # district name.
                city=item.get("location_city") or "Wien",
                country="AT",
                lat=_coordinate(item.get("event_latitude")),
                lon=_coordinate(item.get("event_longitude")),
                categories_raw=["Party", "Electronic"],
                image_url=item.get("event_cover") or None,
            )
