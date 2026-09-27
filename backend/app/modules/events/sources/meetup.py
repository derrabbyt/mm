"""meetup - community, tech and language meetups.

    GET https://www.meetup.com/find/at--vienna/

The page embeds JSON-LD `Event` objects (~49 upcoming). The URL's date
parameters (`customStartDate`, `customEndDate`, `dateRange=custom`) are
**silently ignored** - identical results and an identical date span with and
without them - so the window is left to normalisation.

`streetAddress` is loosely formatted ("Operngasse 7, Vienna") and there are no
coordinates, so these Listings need geocoding before they can be placed.
Meetup's GraphQL API would be better structured but requires OAuth.

**No `image_url`, deliberately.** The JSON-LD `image` is populated, but every
single value across the corpus is one of five
`/images/fallbacks/redesign/group-cover-N-square.webp` placeholders. Storing
those would render the same five stock pictures across every meetup while
looking like real data.
"""

import re
from collections.abc import Iterator

from ..scraped import RawListing, RawOccurrence
from . import jsonld
from .dates import parse_iso_datetime
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="meetup",
    locales=("en",),
    delay_seconds=1.0,
    notes=(
        "Date params (customStartDate/customEndDate/dateRange) are silently "
        "ignored - filter locally. No coordinates; streetAddress is loose free "
        "text. The GraphQL API needs OAuth. The JSON-LD image is always one of "
        "five stock placeholders, so no image is stored."
    ),
)

FIND_URL = "https://www.meetup.com/find/at--vienna/"


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    response = ctx.http.get(FIND_URL)
    yield RawPayload(url=response.url, body=response.body, kind="listing")


_POSTCODE = re.compile(r"\b(\d{4})\b")
_TRAILING_CITY = re.compile(
    r"^(?:\d{4}\s+)?(vienna|wien|austria|österreich)$", re.IGNORECASE
)


def _split_address(
    raw: str | None, locality: str | None
) -> tuple[str | None, str | None, str | None]:
    """Split Meetup's free-text street field into (street, postcode, city).

    Observed shapes, all arriving in the one field:

        "Operngasse 7, Vienna"
        "Währinger Gürtel 1, 1180 Vienna"
        "Alser Strasse 4, 1080"
        "Herrmannpark, Obere Weißgerberstraße, 1030 Wien, Wien"

    Leaving the city or the postcode inside the street makes geocoding worse,
    and duplicates what the Listing already holds in its own columns.
    """
    if not raw:
        return None, None, locality
    parts = [part.strip() for part in str(raw).split(",") if part.strip()]
    postcode = None

    while parts:
        found = _POSTCODE.search(parts[-1])
        if _TRAILING_CITY.match(parts[-1]):
            if found:
                postcode = found.group(1)
            parts.pop()
            continue
        # A trailing "1030 Wien" segment: keep the postcode, drop the rest.
        if found and _TRAILING_CITY.match(_POSTCODE.sub("", parts[-1]).strip()):
            postcode = found.group(1)
            parts.pop()
            continue
        if found and parts[-1].strip() == found.group(1):
            postcode = found.group(1)
            parts.pop()
            continue
        break

    return ", ".join(parts) or None, postcode, locality


def parse(payload: RawPayload) -> Iterator[RawListing]:
    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        url = (node.get("url") or "").split("?")[0]
        if not url:
            continue
        # /<group-slug>/events/<numeric-id>/ - the id is the stable key.
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        source_ref = tail if tail.isdigit() else url.rstrip("/")
        if source_ref in seen:
            continue

        start = parse_iso_datetime(node.get("startDate"))
        if start is None:
            continue
        seen.add(source_ref)

        where = jsonld.place(node)
        street, postcode, city = _split_address(where["street"], where["city"])

        yield RawListing(
            source_ref=source_ref,
            occurrences=[
                RawOccurrence(start=start, end=parse_iso_datetime(node.get("endDate")))
            ],
            url=url,
            title=node.get("name"),
            description=node.get("description"),
            lang="en",
            venue_name=where["venue_name"],
            street=street,
            postcode=postcode,
            city=city or "Wien",
            country="AT",
            # Meetup publishes no per-event taxonomy in the markup; these are
            # community gatherings, closest to a talk or a workshop.
            categories_raw=["Meetup", "Workshop"],
        )
