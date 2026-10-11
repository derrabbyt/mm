import datetime as dt
import logging
from collections.abc import Iterator
from typing import Any

from ....core.http import FetchError
from ..scraped import RawListing, RawOccurrence
from . import jsonld
from .dates import day_if_midnight, paired_end, parse_iso_when
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="wien_gv_at",
    locales=("de",),
    delay_seconds=0.35,
    # The index is server-generated and slow: ~490 KB in ~32s, measured, which
    # sits just past the client's 30s default. That expired mid-run and failed
    # the whole Source for a request that was working fine, just slowly.
    timeout_seconds=90.0,
    notes=(
        "The index JSON is GeoJSON with coordinates but NO dates; dates come "
        "from per-event JSON-LD. subEvent[] is authoritative and may be gapped "
        "- using start..end as a span instead over-counts badly. The index "
        "lists stale URLs that 404; skip them rather than failing the Source."
    ),
)

logger = logging.getLogger(__name__)

INDEX = "https://www.wien.gv.at/veranstaltungen.json"
# The index is the full catalogue, and it grows: ~620 when this was written, 804
# on 2026-09-04. The cap is a runaway guard, not a budget - set it clear of the
# real catalogue, or it silently truncates the Source. At 0.35s the whole index
# costs ~5 minutes.
MAX_DETAILS = 1200
MAX_DETAIL_FAILURES = 40


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    resp = ctx.http.get(INDEX, headers={"Accept": "application/json"})
    yield RawPayload(url=resp.url, body=resp.body, kind="index")

    features = resp.json().get("features") or []

    failures = 0
    for feature in features[:MAX_DETAILS]:
        props = feature.get("properties") or {}
        url = props.get("url")
        if not url:
            continue
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        lon, lat = (list(coords) + [None, None])[:2]
        try:
            detail = ctx.http.get(url)
        except FetchError as exc:
            failures += 1
            logger.warning("wien_gv_at: skipping %s (%s)", url, exc)
            if failures > MAX_DETAIL_FAILURES:
                logger.error(
                    "wien_gv_at: %d detail failures - stopping early", failures
                )
                return
            continue
        yield RawPayload(
            url=detail.url,
            body=detail.body,
            kind="detail",
            meta={
                "id": props.get("id"),
                "lat": lat,
                "lon": lon,
                "image": props.get("image0"),
            },
        )


def _occurrences(node: dict[str, Any]) -> list[RawOccurrence]:
    spans = [one for one in node.get("subEvent") or [] if isinstance(one, dict)] or [
        node
    ]

    found: list[RawOccurrence] = []
    for span in spans:
        start = day_if_midnight(parse_iso_when(span.get("startDate")))
        if start is None:
            continue
        end = day_if_midnight(parse_iso_when(span.get("endDate")))
        if _is_day(start) and isinstance(end, dt.datetime):
            end = end.date()
        found.append(RawOccurrence(start=start, end=paired_end(start, end)))
    return found


def _is_day(moment: dt.datetime | dt.date) -> bool:
    return not isinstance(moment, dt.datetime)


def _address(node: dict[str, Any]) -> dict[str, Any]:
    addresses = node.get("addresses") or node.get("address") or []
    if isinstance(addresses, dict):
        return addresses
    found = jsonld.first(addresses)
    return found if isinstance(found, dict) else {}


def parse(payload: RawPayload) -> Iterator[RawListing]:
    if payload.kind != "detail":
        return

    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        url = node.get("url") or node.get("mainEntityOfPage") or payload.url
        if not isinstance(url, str):
            url = payload.url
        source_ref = str(payload.meta.get("id") or url.rstrip("/").rsplit("/", 1)[-1])
        if source_ref in seen:
            continue

        occurrences = _occurrences(node)
        if not occurrences:
            continue
        seen.add(source_ref)

        address = _address(node)
        organizer = jsonld.mapping(node.get("organizer")).get("name")

        yield RawListing(
            source_ref=source_ref,
            occurrences=occurrences,
            url=url,
            title=node.get("name"),
            description=node.get("description"),
            lang="de",
            venue_name=address.get("name") or address.get("streetAddress"),
            street=address.get("street") or address.get("streetAddress"),
            postcode=address.get("postalCode"),
            city=address.get("addressLocality") or "Wien",
            country=address.get("addressCountry") or "AT",
            lat=payload.meta.get("lat"),
            lon=payload.meta.get("lon"),
            categories_raw=[
                str(prop.get("name"))
                for prop in node.get("additionalProperty") or []
                if isinstance(prop, dict) and prop.get("name")
            ],
            organizer=organizer if isinstance(organizer, str) else None,
            image_url=payload.meta.get("image"),
        )
