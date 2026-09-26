"""goabase - psytrance and goa parties. The smallest, cleanest API of the set.

    GET https://www.goabase.net/api/party/json/?country=AT

Note the **`/json/` path segment** - without it the same path returns HTML.
`limit`, `page` and `dateFrom` are all silently ignored, so one call returns the
complete upcoming set for the country (~25 for AT), some of it a year or more
out. `page=2` returning 0 confirms there is nothing more to page to.

Nothing here honours `FetchContext`'s date window, because there is no request
to put it in and `parse` must stay pure - filtering on "today" here would make
this a different function every day and the fixture tests untestable. What is
plausible is normalisation's job, and its window is deliberately the wider one:
asking a site for sixty days is not the same as refusing to store a run that
lasts longer than that.

Dates are full ISO with an offset, and coordinates are present but **coarse**:
every Vienna party reports 48.2 / 16.3, the city centroid rather than the Venue.
Those are deliberately dropped - a fake pin in the middle of town is worse for
"what is on near here" than no pin at all, and the address fields still allow
real geocoding later.
"""

import json
from collections.abc import Iterator
from typing import Any

from .. import region
from ..scraped import RawListing, RawOccurrence
from .dates import parse_iso_datetime
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="goabase",
    locales=("en",),
    delay_seconds=1.0,
    notes=(
        "The /json/ path segment is required or you get HTML. limit/page/dateFrom "
        "are ignored - one call returns everything, filter locally. Coordinates "
        "are the city centroid (48.2/16.3), not the Venue, so they are dropped."
    ),
)

ENDPOINT = "https://www.goabase.net/api/party/json/?country=AT"

# A genuine Venue coordinate has 4+ decimal places (~10 m). One decimal is
# ~11 km - a city centroid, not a location. The whole feed is like this: 21 of
# 25 parties report exactly (48.2, 16.4) and the rest (48.2, 16.3). Listing them
# all at one pin is worse for "what is on near here" than having no pin, so they
# are dropped and left to the geocoder.
MIN_COORD_DECIMALS = 3


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    response = ctx.http.get(ENDPOINT, headers={"Accept": "application/json"})
    yield RawPayload(url=response.url, body=response.body, kind="partylist")


def _decimals(value: float) -> int:
    text = f"{value!r}"
    return len(text.partition(".")[2].rstrip("0")) if "." in text else 0


def _coords(item: dict[str, Any]) -> tuple[float | None, float | None]:
    """Coordinates, but only when they are precise enough to be a Venue."""
    try:
        lat = float(item.get("geoLat"))  # type: ignore[arg-type]
        lon = float(item.get("geoLon"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, None
    if not lat and not lon:
        return None, None
    if _decimals(lat) < MIN_COORD_DECIMALS or _decimals(lon) < MIN_COORD_DECIMALS:
        return None, None
    return lat, lon


def parse(payload: RawPayload) -> Iterator[RawListing]:
    data = json.loads(payload.text)

    for item in data.get("partylist") or []:
        party_id = item.get("id")
        start = parse_iso_datetime(item.get("dateStart"))
        if not party_id or start is None:
            continue

        town = item.get("nameTown")
        if not region.is_vienna(city=town):
            continue

        latitude, longitude = _coords(item)
        party_type = item.get("nameType")

        yield RawListing(
            source_event_id=str(party_id),
            occurrences=[
                RawOccurrence(start=start, end=parse_iso_datetime(item.get("dateEnd")))
            ],
            url=item.get("urlPartyHtml") or None,
            origin_url=item.get("urlOrganizer") or None,
            title=item.get("nameParty"),
            description=item.get("nameOrganizer"),
            lang="en",
            # The feed has no venue field; the type ("Club", "Open Air") is the
            # closest thing, and the detail JSON's textLocation is free text.
            venue_name=None,
            city=town or "Wien",
            country="AT",
            lat=latitude,
            lon=longitude,
            categories_raw=(
                [party_type, "Psytrance", "Party"] if party_type else ["Party"]
            ),
            organizer=item.get("nameOrganizer") or None,
            image_url=item.get("urlImageMedium") or item.get("urlImageFull") or None,
        )
