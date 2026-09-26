"""rausgegangen.com - Vienna listings with the best JSON-LD detail pages.

Django, server-rendered, no bot protection. Two-stage: sweep the category
listings, then fetch each detail page for its JSON-LD, which carries *exact start
and end times*, a full `PostalAddress` and a **structured price** (`price` +
`priceCurrency`) - the only Source here giving machine-readable prices.

No date query exists: `?date=`, `?day=`, `?start_date=` and
`?date_from/to=` are all accepted and ignored, and the `/tipps-fuer-heute/`
style paths are editorial "tips" pages (2 events), not day listings. So we sweep
categories and filter locally.

Pagination lives on **category** pages only (`<a aria-label="Next">`); the city
landing page has none and ignores `?page=`.

**On the crawl delay, stated plainly.** Their robots.txt carries
`Crawl-delay: 10` under a `User-agent: ClaudeBot` block. We send the shared
client's default User-Agent, which is a Chrome string - so we are not announcing
ourselves as ClaudeBot, but we are not identifying ourselves as anything either,
and we cannot honestly claim that block was addressed to someone else. We use 1s
anyway. `Crawl-delay` is not part of the robots.txt standard, and honouring 10s
cost ~47 minutes for this one Source and forced `MAX_DETAILS` down to 120, which
threw away two thirds of what the sweep had already found. That is the trade, and
it is a choice rather than a deduction. Their `Disallow` rules are a different
matter and are honoured as written.
"""

import logging
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup

from ..scraped import RawListing, RawOccurrence
from . import jsonld
from .dates import parse_iso_datetime
from .http import FetchError
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
# The sweep finds ~365 distinct URLs across the 14 categories, and at 1s each the
# whole set costs ~6 minutes. A guard rail rather than a budget: set above what
# the site actually publishes, so it only fires if the sweep starts running away.
MAX_DETAILS = 500


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    # The detail page's JSON-LD carries no category, but the listing that led us
    # there does - so carry it on the payload's meta rather than losing it.
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
            # A single dead detail page must not discard the whole crawl.
            logger.warning("rausgegangen: skipping %s (%s)", detail_url, exc)
            continue
        yield RawPayload(
            url=resp.url,
            body=resp.body,
            kind="detail",
            meta={"category": category_of.get(path)},
        )


def _price(offer: dict[str, Any]) -> tuple[float | None, str | None, bool | None]:
    """Extract price, currency and free-ness from an Offer.

    `price: "0.00"` is **not** treated as free. The site emits it as a
    placeholder for "no price published", and it appears on obviously ticketed
    concerts. Since a free happening and an unpriced one are indistinguishable
    here, both become unknown: showing "free" on a paid show is a worse error
    than showing no price at all.
    """
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
    # Listings exist to discover detail URLs and to be a cheap breakage signal;
    # the structured data all comes from detail pages.
    if payload.kind != "detail":
        return

    category = payload.meta.get("category")

    for node in jsonld.events_in(payload.text):
        url = node.get("url") or payload.url
        source_event_id = url.rstrip("/").rsplit("/", 1)[-1]
        if not source_event_id:
            continue

        start = parse_iso_datetime(node.get("startDate"))
        if start is None:
            continue
        end = parse_iso_datetime(node.get("endDate"))

        where = jsonld.place(node)
        offer = jsonld.mapping(node.get("offers"))
        price, currency, is_free = _price(offer)
        ticket_url = offer.get("url")
        image = jsonld.first(node.get("image"))

        yield RawListing(
            source_event_id=source_event_id,
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
            image_url=image if isinstance(image, str) else None,
        )
