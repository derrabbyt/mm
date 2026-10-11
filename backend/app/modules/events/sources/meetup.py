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
            categories_raw=["Meetup", "Workshop"],
        )
