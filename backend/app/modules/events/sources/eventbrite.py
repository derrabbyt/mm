"""eventbrite - the best structured data of the HTML Sources.

`start_date` / `end_date` genuinely filter (18 events for a day against 62
unfiltered), and every event ships as JSON-LD with a complete `PostalAddress`
**and** `geo` coordinates - so these Listings need no geocoding.

Eventbrite's official REST API dropped public event *search* years ago, so this
HTML-plus-JSON-LD route is the better one despite the API existing.

`/d/austria--vienna/` is a **radius search**, not a city filter: Bratislava,
Graz, Mödling and Kunžak (CZ) all come back from it. Those are dropped rather
than stored, for the reasons `region.py` sets out.
"""

from collections.abc import Iterator

from .. import region
from ..scraped import RawListing, RawOccurrence
from . import jsonld
from .dates import paired_end, parse_iso_when
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="eventbrite",
    locales=("en",),
    delay_seconds=1.0,
    notes=(
        "start_date/end_date DO filter. JSON-LD carries full address + geo, so "
        "no geocoding is needed. The official API has no public event search. "
        "It is a RADIUS search - Bratislava/Graz/CZ leak in, so results are "
        "region-filtered."
    ),
)

BASE = "https://www.eventbrite.com"
SEARCH = f"{BASE}/d/austria--vienna/events/"
MAX_PAGES = 10


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    for page in range(1, MAX_PAGES + 1):
        url = (
            f"{SEARCH}?start_date={ctx.date_from.isoformat()}"
            f"&end_date={ctx.date_to.isoformat()}&page={page}"
        )
        resp = ctx.http.get(url)
        yield RawPayload(
            url=resp.url, body=resp.body, kind="listing", meta={"page": page}
        )
        if not jsonld.events_in(resp.text):
            break


def parse(payload: RawPayload) -> Iterator[RawListing]:
    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        url = node.get("url") or ""
        # /e/<slug>-tickets-<id> - the trailing id is the stable key.
        source_ref = url.rstrip("/").rsplit("-", 1)[-1] if url else ""
        if not source_ref or source_ref in seen:
            continue

        start = parse_iso_when(node.get("startDate"))
        if start is None:
            continue

        where = jsonld.place(node)
        latitude, longitude = jsonld.coordinates(node)
        if not region.is_vienna(latitude, longitude, where["postcode"], where["city"]):
            continue
        seen.add(source_ref)

        value, currency = jsonld.price(node)

        yield RawListing(
            source_ref=source_ref,
            occurrences=[
                RawOccurrence(
                    start=start,
                    end=paired_end(start, parse_iso_when(node.get("endDate"))),
                )
            ],
            url=url or None,
            title=node.get("name"),
            description=node.get("description"),
            lang="en",
            venue_name=where["venue_name"],
            street=where["street"],
            postcode=where["postcode"],
            city=where["city"] or "Wien",
            country=where["country"] or "AT",
            lat=latitude,
            lon=longitude,
            price_min=value,
            price_currency=currency,
            ticket_url=url or None,
            image_url=jsonld.image(node),
        )
