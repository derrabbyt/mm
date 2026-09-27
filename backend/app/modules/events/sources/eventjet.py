"""eventjet - an Austrian ticketing platform.

`/wp-json/` is **401** (locked) and the listing pages carry no event markup, but
the **detail pages do have JSON-LD `Event`** - which is what makes this Source
viable at all. So: harvest `/e/{slug}/` links from the listing, then fetch each
one.

No date filter exists, so the window is left to normalisation. The platform is
Austria-wide rather than Vienna-only, so results are region-filtered - though
the JSON-LD frequently publishes no address at all, and `region.is_vienna`
deliberately keeps a Listing it knows nothing about.

Two traps worth naming. The platform emits **midnight-UTC stamps for day-level
entries**, and reading those as a real 00:00 start sorts a season ticket above
every evening concert. And the Event node's `image` is usually an unresolved
`{"@id": ...}` reference rather than a URL - it resolves to a 2019 site header
shared by every event - so in practice the page's `og:image` is what gets
stored, that being the one picture the site says is this event's.
"""

from collections.abc import Iterator

from bs4 import BeautifulSoup

from ....core.http import FetchError
from .. import region
from ..scraped import RawListing, RawOccurrence
from . import jsonld, markup
from .dates import day_if_midnight, paired_end, parse_iso_when
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="eventjet",
    locales=("de",),
    delay_seconds=0.8,
    notes=(
        "wp-json is 401 and listings carry no event markup, but DETAIL pages "
        "carry JSON-LD Event. No date filter - sweep and filter locally. "
        "Austria-wide, so region-filtered. Midnight stamps mean 'this day', not "
        "00:00. The Event's own `image` is an unresolved @id to a shared site "
        "header - read og:image instead."
    ),
)

BASE = "https://events.eventjet.at"
MAX_DETAILS = 80


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    resp = ctx.http.get(f"{BASE}/")
    yield RawPayload(url=resp.url, body=resp.body, kind="listing")

    soup = BeautifulSoup(resp.text, "lxml")
    seen: set[str] = set()
    urls: list[str] = []
    for anchor in soup.select("a[href]"):
        href = anchor["href"].split("?")[0]
        if "/e/" not in href:
            continue
        full = href if href.startswith("http") else BASE + href
        if full in seen:
            continue
        seen.add(full)
        urls.append(full)

    for url in urls[:MAX_DETAILS]:
        try:
            detail = ctx.http.get(url)
        except FetchError:
            # One delisted event must not discard the rest of the sweep.
            continue
        yield RawPayload(url=detail.url, body=detail.body, kind="detail")


def parse(payload: RawPayload) -> Iterator[RawListing]:
    if payload.kind != "detail":
        return

    soup = BeautifulSoup(payload.text, "lxml")
    # The page's other <img> tags are "more events" cards, so the only picture
    # that reliably belongs to this event is the one it puts on its preview.
    page_image = markup.og_image(soup)

    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        start = day_if_midnight(parse_iso_when(node.get("startDate")))
        if start is None:
            continue
        end = paired_end(start, parse_iso_when(node.get("endDate")))

        # The node names its own page, and that page's slug is the site's id.
        # A slug can host a series, so the start is part of the identity too.
        slug = (node.get("url") or payload.url).rstrip("/").rsplit("/", 1)[-1]
        source_ref = f"{slug}-{start.isoformat()[:16]}"[:120]
        if source_ref in seen:
            continue

        where = jsonld.place(node)
        latitude, longitude = jsonld.coordinates(node)
        if not region.is_vienna(latitude, longitude, where["postcode"], where["city"]):
            continue
        seen.add(source_ref)

        value, currency = jsonld.price(node)

        yield RawListing(
            source_ref=source_ref,
            occurrences=[RawOccurrence(start=start, end=end)],
            url=node.get("url") or payload.url,
            ticket_url=payload.url,
            title=node.get("name"),
            description=node.get("description"),
            lang="de",
            venue_name=where["venue_name"],
            street=where["street"],
            postcode=where["postcode"],
            city=where["city"] or "Wien",
            country=where["country"] or "AT",
            lat=latitude,
            lon=longitude,
            price_min=value,
            price_currency=currency,
            image_url=jsonld.image(node) or page_image,
        )
