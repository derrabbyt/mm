"""eventjet - an Austrian ticketing platform.

`/wp-json/` is **401** (locked) and the listing pages carry no event markup, but
the **detail pages do have JSON-LD `Event`** - which is what makes this Source
viable at all. So: harvest `/e/{slug}/` links from the listing, then fetch each
one.

No date filter exists, so the window is left to normalisation. The platform is
Austria-wide rather than Vienna-only, so results are region-filtered - though
the JSON-LD frequently publishes no address at all, and `region.is_vienna`
deliberately keeps a Listing it knows nothing about.

**Where a happening is, is not in the JSON-LD at all.** The `Event` node carries
no `location`, so reading only the JSON-LD leaves every Listing unplaced - it
cannot be found near a Rendezvous, and the Source contributes rows nobody sees.
The page has it twice over: `.single__hero-venue-name` and an
`address.address--venue` giving "Street, POSTCODE City, Country", and a pair of
Open Graph meta tags giving coordinates.

Those meta tags are spelt `event:location:latitude` and
**`event:location:longitued`** - the site's own typo, on every page of the
crawl. Reading the correct spelling finds nothing, which is how the position
went unnoticed: the pages look like they publish none.

That matters twice, because the platform is Austria-wide. Without coordinates
`region.is_vienna` keeps a Listing it knows nothing about, so this Source was
quietly contributing Krems, Mödling and Frankfurt to a Vienna catalogue - a
majority of what it returned.

Two more traps. The platform emits **midnight-UTC stamps for day-level
entries**, and reading those as a real 00:00 start sorts a season ticket above
every evening concert. And the Event node's `image` is usually an unresolved
`{"@id": ...}` reference rather than a URL - it resolves to a 2019 site header
shared by every event - so in practice the page's `og:image` is what gets
stored, that being the one picture the site says is this event's.
"""

import re
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
        "Austria-wide, so region-filtered - and the JSON-LD has NO location, so "
        "the venue comes from .single__hero-venue-name plus address.address--venue "
        "and the coordinates from og `event:location:latitude` and "
        "`event:location:longitued`, the site's own typo. Midnight stamps mean "
        "'this day', not 00:00. The Event's own `image` is an unresolved @id to "
        "a shared site header - read og:image instead."
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


_ADDRESS = re.compile(
    r"^(?P<street>.*?),\s*(?P<postcode>\d{4})\s+(?P<city>[^,]+)", re.DOTALL
)


def _coordinates(soup: BeautifulSoup) -> tuple[float | None, float | None]:
    """The position, from the Open Graph tags the site misspells.

    `longitued` first because that is what every page of the crawl actually
    emits; the correct spelling is tried after, so this keeps working on the
    day somebody fixes it.
    """

    def content(key: str) -> str:
        tag = soup.find("meta", attrs={"property": key}) or soup.find(
            "meta", attrs={"name": key}
        )
        return (tag.get("content") or "").strip() if tag else ""

    try:
        latitude = float(content("event:location:latitude"))
        longitude = float(
            content("event:location:longitued") or content("event:location:longitude")
        )
    except ValueError:
        return None, None
    return latitude, longitude


def _venue(soup: BeautifulSoup) -> dict[str, str | None]:
    """Where the page says the happening is, which the JSON-LD does not.

    The address renders as "Street 30, 1040 Wien, Österreich" in one element,
    so it is split here rather than stored whole: a street with the city and
    postcode still in it geocodes worse and duplicates columns of its own.
    """
    name = soup.select_one(".single__hero-venue-name")
    details = soup.select_one("address.address--venue .address__details")
    where: dict[str, str | None] = {
        "venue_name": name.get_text(" ", strip=True) if name else None,
        "street": None,
        "postcode": None,
        "city": None,
    }
    if details is not None:
        found = _ADDRESS.match(details.get_text(" ", strip=True))
        if found:
            where["street"] = found.group("street").strip() or None
            where["postcode"] = found.group("postcode")
            where["city"] = found.group("city").strip() or None
    return where


def parse(payload: RawPayload) -> Iterator[RawListing]:
    if payload.kind != "detail":
        return

    soup = BeautifulSoup(payload.text, "lxml")
    on_page = _venue(soup)
    latitude, longitude = _coordinates(soup)
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

        # The page's own venue block, since the Event node has no `location`.
        where = {**jsonld.place(node), **{k: v for k, v in on_page.items() if v}}
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
