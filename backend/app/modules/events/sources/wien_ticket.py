"""wien_ticket - culture, concerts, theatre. Show-oriented, not day-oriented.

There is no date-filtered listing (`/de/tickets/alle-veranstaltungen` and
`/de/suche?q=` are 404 or empty), so the shape is: harvest show URLs from the
homepage and the category pages, then fetch each show and expand its
performances.

That is worth doing because the show pages are **excellent**: one JSON-LD
`Event` per performance - 69 spanning four months for a single show - each with
a complete `PostalAddress` **and** `GeoCoordinates`.

Since one page yields many performances of the same production, each
performance is its own Listing - they are genuinely separate happenings on
separate nights - keyed by the id the site gives that performance. Only when a
node carries no URL of its own does the key fall back to `{show_id}-{start}`.
"""

import re
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup

from ....core.http import FetchError
from ..scraped import RawListing, RawOccurrence
from . import jsonld
from .dates import parse_iso_when
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="wien_ticket",
    locales=("de",),
    delay_seconds=0.8,
    notes=(
        "No date-filtered listing exists - harvest /de/ticket/{id}/{slug} URLs "
        "then expand each show's performances. One JSON-LD Event PER "
        "PERFORMANCE (69 for one show), each with address + geo and its own "
        "/de/ticket/{id}/ - key on that, or the show collapses to one Listing."
    ),
)

BASE = "https://www.wien-ticket.at"
START_PAGES = (
    f"{BASE}/de/home",
    f"{BASE}/de/kategorie/konzerte",
    f"{BASE}/de/kategorie/theater-kabarett",
)
MAX_SHOWS = 80

SHOW_PATH = re.compile(r"/de/ticket/(\d+)/")


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    show_urls: list[str] = []
    seen: set[str] = set()

    for page_url in START_PAGES:
        try:
            resp = ctx.http.get(page_url)
        except FetchError:
            # Category paths change; the homepage alone is enough to proceed.
            continue
        yield RawPayload(url=resp.url, body=resp.body, kind="listing")

        soup = BeautifulSoup(resp.text, "lxml")
        for anchor in soup.select("a[href]"):
            href = anchor["href"].split("?")[0]
            found = SHOW_PATH.search(href)
            if not found or found.group(1) in seen:
                continue
            seen.add(found.group(1))
            show_urls.append(href if href.startswith("http") else BASE + href)

    for url in show_urls[:MAX_SHOWS]:
        try:
            show = ctx.http.get(url)
        except FetchError:
            # One dead show must not discard the pages already fetched.
            continue
        yield RawPayload(url=show.url, body=show.body, kind="show")


def _performance_id(node: dict[str, Any]) -> str | None:
    """The site's own id for this performance, from the node's own URL.

    Each performance links to `/de/ticket/{id}/` with an id of its own, distinct
    from the show page's - 54 archived show pages, 1,050 performances, no
    collisions and none missing. It is a better key than a synthesised
    show-plus-start because it survives the show page being renumbered, which
    is the one thing a ticketing platform does regularly.
    """
    found = SHOW_PATH.search(node.get("url") or "")
    return found.group(1) if found else None


def parse(payload: RawPayload) -> Iterator[RawListing]:
    # The start pages exist to discover show URLs; every date is on a show page.
    if payload.kind != "show":
        return

    found = SHOW_PATH.search(payload.url)
    show_id = found.group(1) if found else payload.url.rstrip("/").rsplit("/", 2)[-2]

    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        start = parse_iso_when(node.get("startDate"))
        if start is None:
            continue
        # One Listing per performance: separate nights are separate happenings.
        source_ref = _performance_id(node) or f"{show_id}-{start.isoformat()[:16]}"
        if source_ref in seen:
            continue
        seen.add(source_ref)

        where = jsonld.place(node)
        latitude, longitude = jsonld.coordinates(node)

        yield RawListing(
            source_ref=source_ref,
            occurrences=[RawOccurrence(start=start)],
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
            image_url=jsonld.image(node),
        )
