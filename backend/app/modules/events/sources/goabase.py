import json
import logging
from collections.abc import Iterator
from typing import Any

from ....core.http import FetchError
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
        "are the city centroid (48.2/16.3), not the Venue, so they are dropped - "
        "which leaves the per-party urlPartyJsonLD as the only source of a venue "
        "or a street. Its textLocation sibling is empty on every Vienna party, "
        "and a third of the feed calls its venue 'Party Place'."
    ),
)

ENDPOINT = "https://www.goabase.net/api/party/json/?country=AT"

# A genuine Venue coordinate has 4+ decimal places (~10 m). One decimal is
# ~11 km - a city centroid, not a location. The whole feed is like this: 21 of
# 25 parties report exactly (48.2, 16.4) and the rest (48.2, 16.3). Listing them
# all at one pin is worse for "what is on near here" than having no pin, so they
# are dropped and left to the geocoder.
MIN_COORD_DECIMALS = 3

logger = logging.getLogger(__name__)


PLACEHOLDER_VENUE = "party place"


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    response = ctx.http.get(ENDPOINT, headers={"Accept": "application/json"})
    yield RawPayload(url=response.url, body=response.body, kind="partylist")

    for item in json.loads(response.text).get("partylist") or []:
        if not region.is_vienna(city=item.get("nameTown")):
            continue
        url = item.get("urlPartyJsonLD")
        if not url:
            continue
        try:
            detail = ctx.http.get(url, headers={"Accept": "application/ld+json"})
        except FetchError as exc:
            logger.warning("goabase: no detail for %s (%s)", url, exc)
            continue
        yield RawPayload(
            url=detail.url, body=detail.body, kind="party", meta={"party": item}
        )


def _decimals(value: float) -> int:
    text = f"{value!r}"
    return len(text.partition(".")[2].rstrip("0")) if "." in text else 0


def _coords(item: dict[str, Any]) -> tuple[float | None, float | None]:
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


def _from_list_item(item: dict[str, Any]) -> RawListing | None:
    party_id = item.get("id")
    start = parse_iso_datetime(item.get("dateStart"))
    if not party_id or start is None:
        return None

    town = item.get("nameTown")
    latitude, longitude = _coords(item)
    party_type = item.get("nameType")

    return RawListing(
        source_ref=str(party_id),
        occurrences=[
            RawOccurrence(start=start, end=parse_iso_datetime(item.get("dateEnd")))
        ],
        url=item.get("urlPartyHtml") or None,
        origin_url=item.get("urlOrganizer") or None,
        title=item.get("nameParty"),
        description=item.get("nameOrganizer"),
        lang="en",
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


def _venue(node: dict[str, Any]) -> dict[str, Any]:
    place = node.get("location") or {}
    name = (place.get("name") or "").strip()
    address = place.get("address") or {}
    return {
        "venue_name": None if name.casefold() == PLACEHOLDER_VENUE else (name or None),
        "street": (address.get("streetAddress") or "").strip() or None,
        "postcode": address.get("postalCode"),
        "city": address.get("addressLocality"),
    }


def _parse_detail(payload: RawPayload) -> Iterator[RawListing]:
    node = json.loads(payload.text)
    item = payload.meta.get("party") or {}

    listing = _from_list_item(item)
    if listing is None:
        return

    where = _venue(node)
    offer = node.get("offers") or {}
    price = offer.get("price")

    # Rebuilt rather than `model_copy`d: copying skips validation, and this
    # feed writes its postcode as an integer - the coercion that makes it a
    # string lives in the model, so the update has to go through it.
    yield RawListing(
        **{
            **listing.model_dump(),
            "venue_name": where.get("venue_name"),
            "street": where.get("street"),
            "postcode": where.get("postcode"),
            "city": where.get("city") or listing.city,
            "price_min": float(price) if price is not None else None,
            "price_currency": offer.get("priceCurrency"),
            "ticket_url": offer.get("url"),
        }
    )


def parse(payload: RawPayload) -> Iterator[RawListing]:
    if payload.kind == "party":
        yield from _parse_detail(payload)
        return

    data = json.loads(payload.text)

    for item in data.get("partylist") or []:
        if not region.is_vienna(city=item.get("nameTown")):
            continue
        listing = _from_list_item(item)
        if listing is not None:
            yield listing
