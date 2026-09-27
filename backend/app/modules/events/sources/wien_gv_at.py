"""wien_gv_at - the City of Vienna's official calendar. The richest per-Listing
data of the set.

Two stages, because the two halves of the data live in different places:

* `/veranstaltungen.json` is a **GeoJSON FeatureCollection** with every event's
  title, url, description, image and - importantly - its **coordinates**. It
  has **no dates at all**.
* each `/veranstaltungen/{slug}` detail page carries JSON-LD with the dates, a
  full address and a rich tag list.

So the index gives geo cheaply and the detail pass gives time. Nothing else
here exposes accessibility information; `additionalProperty` includes
"Barrierefreier Zugang", which is preserved in `categories_raw`.

Occurrence trap: `subEvent[]` lists **explicit per-occurrence dates**, and a
series may be gapped. Treating `startDate..endDate` as a span when a `subEvent`
list exists inflated a day count from 49 to 86 during research - always prefer
the explicit list.
"""

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
# Stale index entries 404; tolerate a reasonable number before concluding the
# site itself is unwell.
MAX_DETAIL_FAILURES = 40


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    # The index is the primary request: if it fails, the Source has failed and
    # the exception should propagate.
    resp = ctx.http.get(INDEX, headers={"Accept": "application/json"})
    yield RawPayload(url=resp.url, body=resp.body, kind="index")

    # A malformed index is a Source failure, not an empty day. Swallowing it
    # here returned 0 Listings with fetched_ok=True, so the run looked healthy
    # while the second-largest Source silently contributed nothing.
    features = resp.json().get("features") or []

    failures = 0
    for feature in features[:MAX_DETAILS]:
        props = feature.get("properties") or {}
        url = props.get("url")
        if not url:
            continue
        # Carry the index's coordinates through; the detail page's own geo is
        # not always populated, and this saves re-deriving them.
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        lon, lat = (list(coords) + [None, None])[:2]
        try:
            detail = ctx.http.get(url)
        except FetchError as exc:
            # The index lists stale entries that 404 (e.g. ma-59-ordner). One
            # dead link must not discard the hundreds of pages already fetched.
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
    """Prefer the explicit subEvent list; fall back to the top-level span.

    The city publishes many occurrences at exactly 00:00, which means "on this
    day" rather than "at midnight", so those become all-day.
    """
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
            # The city pairs a midnight start with a 23:59 end, meaning the
            # whole day. Read at face value the end is a clock time against a
            # day-level start, which `paired_end` would drop - and dropping it
            # would collapse a season that runs 00:00 to 23:59 on its last day.
            end = end.date()
        found.append(RawOccurrence(start=start, end=paired_end(start, end)))
    return found


def _is_day(moment: dt.datetime | dt.date) -> bool:
    return not isinstance(moment, dt.datetime)


def _address(node: dict[str, Any]) -> dict[str, Any]:
    """The city's own address shape, which is `addresses` rather than schema's.

    Not `jsonld.place`: these pages put the venue under a plural `addresses`
    key with a `street` field of its own, so the shared reader finds nothing.
    """
    addresses = node.get("addresses") or node.get("address") or []
    if isinstance(addresses, dict):
        return addresses
    found = jsonld.first(addresses)
    return found if isinstance(found, dict) else {}


def parse(payload: RawPayload) -> Iterator[RawListing]:
    # The index has no dates, so it yields nothing; it is fetched for its
    # coordinates (passed via meta) and as a cheap breakage signal.
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
            # The tags carry real signal, including accessibility, which
            # nothing else here provides.
            categories_raw=[
                str(prop.get("name"))
                for prop in node.get("additionalProperty") or []
                if isinstance(prop, dict) and prop.get("name")
            ],
            organizer=organizer if isinstance(organizer, str) else None,
            image_url=payload.meta.get("image"),
        )
