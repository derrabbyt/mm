import json
import re
from collections.abc import Iterator
from typing import Any

from ..scraped import RawListing, RawOccurrence
from .dates import parse_iso_datetime
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="bandsintown",
    locales=("en",),
    delay_seconds=1.0,
    notes=(
        "MUST send page_type=cityPage and follow urlForNextPageOfEvents; blind "
        "page=N re-serves the last page forever. hasMoreEvents is UI state, not "
        "completeness. Radius search: filter locationText on "
        "^(wien|vienna), austria$. The landing page embeds events WITHOUT "
        "locationText - start at page=1 instead."
    ),
)

BASE = "https://www.bandsintown.com"
CITY = "vienna-austria"
LAT, LON = 48.20849, 16.37208
SCOPE = "all-dates"
MAX_PAGES = 80

VIENNA_TEXT = re.compile(r"^\s*(wien|vienna)\s*,\s*austria\s*$", re.IGNORECASE)


def first_url() -> str:
    return (
        f"{BASE}/{SCOPE}/fetch-next/upcomingEvents?page=1"
        f"&longitude={LON}&latitude={LAT}&genre_query=all-genres&page_type=cityPage"
    )


def extract_state(html: str) -> dict[str, Any] | None:
    marker = re.search(r"window\.__data\s*=\s*", html)
    if not marker:
        return None
    index = marker.end()
    while index < len(html) and html[index] not in "{[":
        index += 1
    depth = 0
    in_string = escaped = False
    for position in range(index, len(html)):
        char = html[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[index : position + 1])
                except json.JSONDecodeError:
                    return None
    return None


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    landing_url = f"{BASE}/c/{CITY}/{SCOPE}/genre/all-genres"
    landing = ctx.http.get(landing_url)
    yield RawPayload(url=landing.url, body=landing.body, kind="landing")

    url: str | None = first_url()
    for _ in range(MAX_PAGES):
        if not url:
            break
        resp = ctx.http.get(
            url,
            headers={
                "Accept": "application/json",
                "Referer": landing_url,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        yield RawPayload(url=resp.url, body=resp.body, kind="page")
        try:
            data = resp.json()
        except ValueError:
            break
        # The authoritative end-of-feed signal. Never infer the end from the
        # page size, and never from hasMoreEvents.
        url = data.get("urlForNextPageOfEvents") if isinstance(data, dict) else None


def _events_in(payload: RawPayload) -> list[dict[str, Any]]:
    if payload.kind == "landing":
        return []
    try:
        data = json.loads(payload.text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return data
    return data.get("events") or []


def parse(payload: RawPayload) -> Iterator[RawListing]:
    for item in _events_in(payload):
        if not isinstance(item, dict):
            continue
        if not VIENNA_TEXT.match(item.get("locationText") or ""):
            continue

        event_id = item.get("id")
        start = parse_iso_datetime(item.get("startsAt"))
        if not event_id or start is None:
            continue

        artist = item.get("artistName")
        title = item.get("title") or artist
        if not title:
            continue

        yield RawListing(
            source_ref=str(event_id),
            occurrences=[RawOccurrence(start=start)],
            url=(item.get("eventUrl") or "").split("?")[0] or None,
            origin_url=(item.get("callToActionRedirectUrl") or "").split("?")[0]
            or None,
            title=title,
            description=artist,
            lang="en",
            venue_name=item.get("venueName"),
            city="Wien",
            country="AT",
            categories_raw=["Konzert"],
            image_url=item.get("properlySizedImageURL") or item.get("artistImageSrc"),
            organizer=artist,
        )
