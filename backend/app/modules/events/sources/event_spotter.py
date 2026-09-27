"""event-spotter.com - a venue-primary aggregator that cites its sources.

Next.js, no bot protection, and the JSON-LD is on the **listing** pages, so one
paginated crawl gets everything: 570 Vienna Listings over ~3 months in 57
requests, 560 of them with a street address.

Its distinguishing feature is `sameAs` on **every** entry - the URL of the venue
page it was scraped from. That is an exact-match identity signal, far better than
the (title, date, venue) heuristic everything else needs, and it is kept in
`origin_url`.

The canonical route is `/en/events-in-vienna`; `/en/veranstaltungen-in-wien` from
the regions page redirects there. Pagination is a `rel="Next"` link, which also
carries `id="load-more-link"`, and it terminates cleanly.

The paths robots.txt disallows are not fetched: `/*/calendar`, `/*/admin`,
`/*/favored`, `/*/myevents`, `/*/tags*`, `/*/approval`. The sweep never
constructs one, since it only ever follows the next-page link.
"""

from collections.abc import Iterator

from bs4 import BeautifulSoup

from ..scraped import RawListing, RawOccurrence
from . import jsonld
from .dates import parse_iso_datetime
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="event_spotter",
    locales=("en",),
    delay_seconds=0.6,
    notes=(
        "JSON-LD is on the LISTING pages, so no detail fetch is needed. Follow "
        "rel='Next' (terminates cleanly). Every entry has sameAs = the origin "
        "venue URL, an exact identity signal. Canonical path /en/events-in-vienna."
    ),
)

BASE = "https://event-spotter.com"
START = f"{BASE}/en/events-in-vienna"
MAX_PAGES = 80


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    url: str | None = START
    for _ in range(MAX_PAGES):
        if not url:
            break
        resp = ctx.http.get(url)
        yield RawPayload(url=resp.url, body=resp.body, kind="listing")

        soup = BeautifulSoup(resp.text, "lxml")
        next_link = soup.find("a", attrs={"rel": "Next"}) or soup.find(
            "a", id="load-more-link"
        )
        href = next_link.get("href") if next_link else None
        if not href:
            break
        url = href if href.startswith("http") else BASE + href


def parse(payload: RawPayload) -> Iterator[RawListing]:
    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        url = node.get("url") or ""
        # /en/event/{slug}-{id}: the trailing numeric id is the stable key.
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        source_ref = tail.rsplit("-", 1)[-1] if "-" in tail else tail
        if not source_ref or source_ref in seen:
            continue

        start = parse_iso_datetime(node.get("startDate"))
        if start is None:
            continue
        seen.add(source_ref)
        end = parse_iso_datetime(node.get("endDate"))

        where = jsonld.place(node)

        # The venue page this was scraped from: an exact identity signal.
        same_as = jsonld.first(node.get("sameAs"))

        city = where["city"]
        # A few rows carry junk in this field - "0000", or a street name.
        if city and (city.isdigit() or len(city) > 40):
            city = None

        image = jsonld.first(node.get("image"))

        yield RawListing(
            source_ref=source_ref,
            occurrences=[RawOccurrence(start=start, end=end)],
            url=url or None,
            origin_url=same_as if isinstance(same_as, str) else None,
            title=node.get("name"),
            description=node.get("description"),
            lang="en",
            venue_name=where["venue_name"],
            street=where["street"],
            postcode=where["postcode"],
            city=city or "Wien",
            country="AT",
            image_url=image if isinstance(image, str) else None,
        )
