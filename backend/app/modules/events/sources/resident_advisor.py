import json
from collections.abc import Iterator
from typing import Any

from ..scraped import RawListing, RawOccurrence
from .dates import day_if_midnight, parse_iso_datetime, parse_iso_when
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="resident_advisor",
    locales=("en",),
    delay_seconds=1.0,
    notes=(
        "GraphQL is open; HTML is Cloudflare-403. MUST use eventListings - the "
        "`events` root field returns 0 for every type except TODAY. Vienna is "
        "area 450."
    ),
)

ENDPOINT = "https://ra.co/graphql"
REFERER = "https://ra.co/events/at/vienna"
VIENNA_AREA = 450
PAGE_SIZE = 50
MAX_PAGES = 20

QUERY = """
query($filters: FilterInputDtoInput, $page: Int, $pageSize: Int) {
  eventListings(filters: $filters, page: $page, pageSize: $pageSize) {
    totalResults
    data {
      listingDate
      event {
        id title date startTime endTime contentUrl attending isTicketed
        venue { name contentUrl area { name } }
        artists { name }
        genres { name }
        images { filename type }
      }
    }
  }
}
"""


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    for page in range(1, MAX_PAGES + 1):
        variables = {
            "page": page,
            "pageSize": PAGE_SIZE,
            "filters": {
                "areas": {"eq": VIENNA_AREA},
                "listingDate": {
                    "gte": ctx.date_from.isoformat(),
                    "lte": ctx.date_to.isoformat(),
                },
            },
        }
        resp = ctx.http.post_json(
            ENDPOINT,
            {"query": QUERY, "variables": variables},
            headers={"Referer": REFERER},
        )
        yield RawPayload(
            url=resp.url, body=resp.body, kind="graphql", meta={"page": page}
        )

        try:
            data = resp.json()
        except ValueError:
            return
        listings = _listings(data)
        items = listings.get("data") or []
        total = int(listings.get("totalResults") or 0)
        if len(items) < PAGE_SIZE or page * PAGE_SIZE >= total:
            return


def _listings(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    found = (data.get("data") or {}).get("eventListings")
    return found if isinstance(found, dict) else {}


def parse(payload: RawPayload) -> Iterator[RawListing]:
    data = json.loads(payload.text)
    if data.get("errors"):
        return

    for item in _listings(data).get("data") or []:
        event = item.get("event") or {}
        event_id = event.get("id")
        if not event_id:
            continue

        start = parse_iso_datetime(event.get("startTime"))
        if start is None:
            day = day_if_midnight(
                parse_iso_when(item.get("listingDate") or event.get("date"))
            )
            if day is None:
                continue
            occurrence = RawOccurrence(start=day)
        else:
            occurrence = RawOccurrence(
                start=start, end=parse_iso_datetime(event.get("endTime"))
            )

        venue = event.get("venue") or {}
        artists = [
            one.get("name") for one in event.get("artists") or [] if one.get("name")
        ]
        genres = [
            one.get("name") for one in event.get("genres") or [] if one.get("name")
        ]

        path = event.get("contentUrl") or ""
        url = f"https://ra.co{path}" if path.startswith("/") else (path or None)

        yield RawListing(
            source_ref=str(event_id),
            occurrences=[occurrence],
            url=url,
            title=event.get("title"),
            description=", ".join(artists) if artists else None,
            lang="en",
            venue_name=venue.get("name"),
            city="Wien",
            country="AT",
            categories_raw=genres + ["Party"],
            organizer=artists[0] if artists else None,
            image_url=_image(event),
        )


def _image(event: dict[str, Any]) -> str | None:
    images = [
        one
        for one in event.get("images") or []
        if isinstance(one, dict) and one.get("filename")
    ]
    images.sort(key=lambda one: str(one.get("type")) != "FLYERFRONT")
    return images[0]["filename"] if images else None
