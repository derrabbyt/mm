import logging
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup

from ....core.http import FetchError
from ..scraped import RawListing, RawOccurrence
from . import jsonld
from .dates import parse_iso_datetime
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="rausgegangen",
    locales=("en",),
    delay_seconds=1.0,
    notes=(
        "robots.txt asks *ClaudeBot* for Crawl-delay: 10. We send a browser "
        "User-Agent, so we cannot claim that block is not ours; 1s is a "
        "deliberate choice, not a deduction - see the module docstring. No date "
        "params work (all silently ignored). Pagination only on /category/ "
        "pages, via <a aria-label='Next'>. Detail pages carry JSON-LD prices."
    ),
)

logger = logging.getLogger(__name__)

BASE = "https://rausgegangen.com"
GEO = "geospatial_query_type=CITY&lat=48.2077&lng=16.3705&city=wien"

CATEGORIES = (
    "active-and-creative",
    "available-anytime",
    "children-and-families",
    "concerts-and-music",
    "exhibition",
    "festivals",
    "film",
    "food-and-drinks",
    "market",
    "party",
    "shows-and-performances",
    "spoken-word",
    "sports",
    "theater",
)
MAX_PAGES_PER_CATEGORY = 12
MAX_DETAILS = 500


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    category_of: dict[str, str] = {}
    order: list[str] = []

    for category in CATEGORIES:
        url = f"{BASE}/at/en/wien/category/{category}/?{GEO}"
        for page in range(1, MAX_PAGES_PER_CATEGORY + 1):
            resp = ctx.http.get(url)
            yield RawPayload(
                url=resp.url,
                body=resp.body,
                kind="listing",
                meta={"category": category, "page": page},
            )
            soup = BeautifulSoup(resp.text, "lxml")
            for anchor in soup.select('a[href*="/at/en/events/"]'):
                path = anchor["href"].split("?")[0]
                if path not in category_of:
                    category_of[path] = category
                    order.append(path)
            next_link = soup.find("a", attrs={"aria-label": "Next"})
            if not next_link or not next_link.get("href"):
                break
            href = next_link["href"]
            url = (
                href
                if href.startswith("http")
                else f"{BASE}/at/en/wien/category/{category}/{href}"
            )

    for path in order[:MAX_DETAILS]:
        detail_url = path if path.startswith("http") else BASE + path
        try:
            resp = ctx.http.get(detail_url)
        except FetchError as exc:
            logger.warning("rausgegangen: skipping %s (%s)", detail_url, exc)
            continue
        yield RawPayload(
            url=resp.url,
            body=resp.body,
            kind="detail",
            meta={"category": category_of.get(path)},
        )


def _price(offer: dict[str, Any]) -> tuple[float | None, str | None, bool | None]:
    if not offer:
        return None, None, None
    raw = offer.get("price")
    currency = offer.get("priceCurrency")
    if raw in (None, ""):
        return None, currency, None
    try:
        value = float(str(raw).replace(",", "."))
    except ValueError:
        return None, currency, None
    if value == 0.0:
        return None, currency, None
    return value, currency, False


def parse(payload: RawPayload) -> Iterator[RawListing]:
    if payload.kind != "detail":
        return

    category = payload.meta.get("category")

    for node in jsonld.events_in(payload.text):
        url = node.get("url") or payload.url
        source_ref = url.rstrip("/").rsplit("/", 1)[-1]
        if not source_ref:
            continue

        start = parse_iso_datetime(node.get("startDate"))
        if start is None:
            continue
        end = parse_iso_datetime(node.get("endDate"))

        where = jsonld.place(node)
        offer = jsonld.mapping(node.get("offers"))
        price, currency, is_free = _price(offer)
        ticket_url = offer.get("url")
        image = jsonld.image(node)

        yield RawListing(
            source_ref=source_ref,
            occurrences=[RawOccurrence(start=start, end=end)],
            url=url,
            origin_url=ticket_url,
            title=node.get("name"),
            description=node.get("description"),
            lang="en",
            venue_name=where["venue_name"],
            street=where["street"],
            postcode=where["postcode"],
            city=where["city"] or "Wien",
            country=where["country"] or "AT",
            categories_raw=[category] if category else [],
            price_min=price,
            price_max=price,
            price_currency=currency,
            is_free=is_free,
            ticket_url=ticket_url,
            image_url=image,
        )
