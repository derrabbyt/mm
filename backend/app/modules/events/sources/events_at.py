"""events.at - a broad commercial calendar with a native per-day query.

Two things about their URL scheme are easy to get wrong, and both fail *silently*:

1. `/calendar/date/{per_page}/{page}` is **pagination, not a date**. The day is
   the `date=YYYY-MM-DD` query parameter; omit it and the site quietly returns
   today's instead of erroring.
2. The `[]` in `state[]` / `event_type[]` **must stay literal**. Sent as
   `%5B%5D` the site answers 200 with the filters ignored, so the whole day
   comes back and it looks like the filter matched everything. This is why the
   HTTP client is built on stdlib `urllib` - see `core/http.py`.

Each happening appears twice per listing - a desktop `card--horizontal` and a
mobile `card--event-timeline` inside one `div.searchResults__timeline` - so the
results are deduplicated on the URL.

The card's `<time datetime>` is the *series'* next showtime, not the queried
day's, so it is deliberately ignored: the queried date is authoritative.
"""

import datetime as dt
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup
from bs4.element import Tag

from ....core.http import encode_query
from ..scraped import RawListing, RawOccurrence
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="events_at",
    locales=("de",),
    delay_seconds=0.5,
    notes=(
        "Literal [] brackets are load-bearing: %5B%5D returns 200 with filters "
        "ignored. /date/{per_page}/{page} is pagination; the day is ?date=. "
        "Card <time> is the series' next showtime, not the queried day's."
    ),
)

BASE = "https://events.at"
PER_PAGE = 200
MAX_PAGES = 10

EVENT_TYPES = (
    "theater",
    "konzert",
    "kinder",
    "party",
    "ball",
    "kabarett",
    "diverses",
    "festival",
    "outdoor",
    "musikfestival",
    "messe",
)
STATES = ("Wien",)

_SLUG_ID = re.compile(r"/event/([a-z0-9\-]+)")

# The marker for one result. Read as a selector when parsing and counted when
# paging, so it is named once.
BLOCK_CLASS = "searchResults__timeline"


def _url(date: dt.date, page: int) -> str:
    params: list[tuple[str, str]] = [("date", date.isoformat())]
    params += [("state[]", s) for s in STATES]
    params += [("event_type[]", t) for t in EVENT_TYPES]
    return f"{BASE}/calendar/date/{PER_PAGE}/{page}?{encode_query(params)}"


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    for day in ctx.dates():
        for page in range(1, MAX_PAGES + 1):
            resp = ctx.http.get(_url(day, page))
            payload = RawPayload(
                url=resp.url,
                body=resp.body,
                kind="listing",
                meta={"date": day.isoformat()},
            )
            yield payload
            # A short page is the last page.
            if _count_blocks(resp.text) < PER_PAGE:
                break


def _count_blocks(html: str) -> int:
    """How many result blocks a page holds, as a cheap "is it full?" test.

    Counted on the class name alone rather than on the whole `class="grid
    searchResults__timeline"` attribute: the same marker is read by `parse` as a
    selector, and requiring a literal attribute string means reordering the
    classes stops paging silently. A string count rather than a parse because
    this runs per page on a ~100 KB document only to decide whether to ask for
    the next one.
    """
    return html.count(BLOCK_CLASS)


def _best_image(card: Tag) -> str | None:
    img = card.select_one(
        "figure img[data-srcset], figure img[data-src], figure img[src]"
    )
    if img is None:
        return None
    srcset = img.get("data-srcset") or img.get("srcset")
    if srcset:
        best, best_width = None, -1
        for candidate in srcset.split(","):
            parts = candidate.strip().rsplit(" ", 1)
            if len(parts) != 2 or not parts[1].endswith("w"):
                continue
            try:
                width = int(parts[1][:-1])
            except ValueError:
                continue
            if width > best_width:
                best, best_width = parts[0].strip(), width
        if best:
            return best
    return img.get("data-src") or img.get("src")


def parse(payload: RawPayload) -> Iterator[RawListing]:
    date_str = payload.meta.get("date")
    if not date_str:
        return
    day = dt.date.fromisoformat(date_str)

    soup = BeautifulSoup(payload.text, "lxml")
    container = soup.select_one("#calendarContainer")
    if container is None:
        return

    seen: set[str] = set()
    for block in container.select(f"div.{BLOCK_CLASS}"):
        # The horizontal card carries the teaser text; fall back to whichever
        # card is present.
        card = block.select_one("article.card--horizontal") or block.select_one(
            "article.card"
        )
        if card is None:
            continue
        link = card.select_one("h3.card-title a[href]")
        if link is None:
            continue
        url = link["href"]
        if url in seen:
            continue
        seen.add(url)

        slug_match = _SLUG_ID.search(url)
        source_ref = (
            slug_match.group(1) if slug_match else url.rstrip("/").rsplit("/", 1)[-1]
        )

        venue_link = card.select_one("a.event-location[href]")
        venue_name = None
        if venue_link is not None:
            venue_el = venue_link.select_one(".event-location__text")
            venue_name = venue_el.get_text(" ", strip=True) if venue_el else None

        teaser_el = card.select_one("p.card-text") or block.select_one("p.card-text")

        yield RawListing(
            source_ref=source_ref,
            # The queried day is authoritative; no clock time in the listing.
            occurrences=[RawOccurrence(start=day)],
            url=url,
            title=link.get_text(" ", strip=True),
            description=teaser_el.get_text(" ", strip=True) if teaser_el else None,
            lang="de",
            venue_name=venue_name,
            city="Wien",
            country="AT",
            image_url=_best_image(card) or _best_image(block),
        )
