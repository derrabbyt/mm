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
            if not found or not soup.select_one('a[href*="page="]'):
                break


def parse(payload: RawPayload) -> Iterator[RawListing]:
    reference = dates.parse_date(payload.meta.get("date") or "")
    if reference is None:
        return

    soup = BeautifulSoup(payload.text, "lxml")

    for card in soup.select("article.content-list-item, article.content-card"):
        anchor = card.select_one('a[href*="_e"]')
        if anchor is None:
            continue
        href = anchor["href"]
        found = _EVENT_ID.search(href)
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
        if image_url and image_url.startswith("data:"):
            image_url = None

        yield RawListing(
            source_ref=str(source_ref),
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
            categories_raw=([category.group(1).replace("-", " ")] if category else []),
            image_url=image_url,
        )


def _date_and_venue(card: Tag, reference: date) -> tuple[str | None, str | None]:
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
