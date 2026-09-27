"""meinbezirk - hyper-local Austrian listings (Regionalmedien Austria).

The highest-volume day-queryable Source after wien.info, and it reaches
genuinely local happenings the ticketing platforms never carry.

The date filter is a plain GET form:

    /event/wien/list?eventitem_filter_simple[date_start]=…&[date_end]=…

Unlike events.at, bracket encoding does **not** matter here (literal and
`%5B%5D` both work) - but `encode_query` is used anyway for consistency, and
because `curl` chokes on literal brackets without `-g` if anyone reproduces
this by hand.

Two parsing traps:

* dates are rendered in **German long form** ("15. August 2026") with no
  `<time datetime>` anywhere, so a `\\d{2}\\.\\d{2}\\.\\d{4}` regex silently
  matches nothing and the filter looks broken when it is not;
* every event appears **twice** per card (image link and title link), so the
  cards are iterated rather than the anchors.
"""

import re
from collections.abc import Iterator
from datetime import date

from bs4 import BeautifulSoup
from bs4.element import Tag

from ....core.http import encode_query
from ..scraped import RawListing, RawOccurrence
from . import dates
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="meinbezirk",
    locales=("de",),
    delay_seconds=0.6,
    notes=(
        "Dates are German long form ('15. August 2026'); there are no "
        "<time datetime> attributes. Each event appears twice per card - "
        "iterate cards, not anchors. The category lives in the URL path "
        "(c-konzert-...), not in the markup."
    ),
)

BASE = "https://www.meinbezirk.at"
LIST_URL = f"{BASE}/event/wien/list"
MAX_PAGES = 12

_EVENT_ID = re.compile(r"_e(\d+)$")
_CATEGORY = re.compile(r"/c-([a-z0-9\-]+)/")


def _url(date_from: date, date_to: date, page: int) -> str:
    params = [
        ("eventitem_filter_simple[date_start]", date_from.isoformat()),
        ("eventitem_filter_simple[date_end]", date_to.isoformat()),
    ]
    if page > 1:
        params.append(("page", str(page)))
    return f"{LIST_URL}?{encode_query(params)}"


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    # One request per day keeps each payload attributable to a known date, which
    # matters because the cards' own dates need a reference year to resolve -
    # and that is what keeps `parse` a pure function of its payload.
    for day in ctx.dates():
        for page in range(1, MAX_PAGES + 1):
            resp = ctx.http.get(_url(day, day, page))
            yield RawPayload(
                url=resp.url,
                body=resp.body,
                kind="listing",
                meta={"date": day.isoformat(), "page": page},
            )
            soup = BeautifulSoup(resp.text, "lxml")
            found = {
                a["href"] for a in soup.select("a[href]") if _EVENT_ID.search(a["href"])
            }
            # No next-page link in the markup, so stop when a page adds nothing.
            if not found or not soup.select_one('a[href*="page="]'):
                break


def parse(payload: RawPayload) -> Iterator[RawListing]:
    reference = dates.parse_date(payload.meta.get("date") or "")
    if reference is None:
        return

    soup = BeautifulSoup(payload.text, "lxml")

    # One <article> per event. Both the image link and the headline link point
    # at the same event, so iterating cards removes the duplication rather than
    # having to filter it out afterwards.
    for card in soup.select("article.content-list-item, article.content-card"):
        anchor = card.select_one('a[href*="_e"]')
        if anchor is None:
            continue
        href = anchor["href"]
        found = _EVENT_ID.search(href)
        # The id is also on the article itself; prefer that when present.
        source_ref = card.get("data-eventitem-id") or (
            found.group(1) if found else None
        )
        if not source_ref:
            continue

        headline = card.select_one("h3.content-card-headline, h3, h2")
        title = (headline or anchor).get_text(" ", strip=True)
        if not title:
            continue

        date_text, venue = _date_and_venue(card, reference)

        category = _CATEGORY.search(href)

        teaser = card.select_one(".content-card-text, .teaser, p")
        image = card.select_one("img[data-src], img[src]")
        image_url = (
            (image.get("data-src") or image.get("src")) if image is not None else None
        )
        # Lazy-loaded cards carry a base64 placeholder in src until scrolled to.
        if image_url and image_url.startswith("data:"):
            image_url = None

        yield RawListing(
            source_ref=str(source_ref),
            # The queried day is authoritative - the filter guarantees it - so
            # the card text is mined only for a clock time, which the filter
            # cannot supply.
            occurrences=[
                RawOccurrence(
                    start=dates.combine(reference, dates.parse_time(date_text or ""))
                )
            ],
            url=href if href.startswith("http") else BASE + href,
            title=title,
            description=teaser.get_text(" ", strip=True) if teaser else None,
            lang="de",
            venue_name=venue,
            city="Wien",
            country="AT",
            # "c-konzert-buehne-kino" -> "konzert buehne kino"
            categories_raw=([category.group(1).replace("-", " ")] if category else []),
            image_url=image_url,
        )


def _date_and_venue(card: Tag, reference: date) -> tuple[str | None, str | None]:
    """Tell the date apart from the venue in the card's one location list.

    `<ul class="content-card-date-location">` holds date, venue and city as
    sibling `<li>`s with nothing to distinguish them, so the date is found by
    being readable as one and "Wien" is refused as a venue name.
    """
    date_text = venue = None
    for item in card.select("ul.content-card-date-location li"):
        text = item.get_text(" ", strip=True)
        if not text:
            continue
        if date_text is None and dates.parse_date(text, reference) is not None:
            date_text = text
        elif venue is None and text.lower() not in {"wien", "vienna"}:
            venue = text
    return date_text, venue
