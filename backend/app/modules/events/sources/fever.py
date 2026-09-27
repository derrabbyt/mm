"""fever - immersive experiences, Candlelight concerts, themed events.

The city page is an Astro app whose embedded `application/json` config carries
`page-config.cityPlansIds` - every Vienna plan id - and the page also links the
plans directly as `/m/{planId}`. Both are read, because neither is complete on
its own. Those plan pages are **server-rendered with full JSON-LD**: `Event`,
`Place`, `PostalAddress`, `GeoCoordinates` and `Offer`, so dates *and*
coordinates are available.

Date filtering is keyword-only: `/de/wien/when/{today|tomorrow|this-weekend}`
work, while `/when/2026-08-15` and `/when/weekend` are 404. So the window is
left to normalisation.

The internal API hosts are discoverable (`data-search.apigw.feverup.com` and
friends, plus an `applicationId`) but every guessed route 404s; the plan pages
give better-structured data anyway.

A plan is one happening, and the plan id is what identifies it - so a page
carrying a second `Event` node would be the same Listing twice. Sixty archived
plan pages carry one Event node or none, so that is a guard rather than an
observed repeat. The `Product` node alongside it holds the same data but is not
an Event, so it never reaches a Listing.
"""

import json
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup

from ....core.http import FetchError
from ..scraped import RawListing, RawOccurrence
from . import jsonld
from .dates import paired_end, parse_iso_when
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="fever",
    locales=("de",),
    delay_seconds=1.0,
    notes=(
        "The city page config holds page-config.cityPlansIds; plan pages "
        "/m/{id} are server-rendered with JSON-LD incl. GeoCoordinates. "
        "/when/ takes only keywords (today|tomorrow|this-weekend) - arbitrary "
        "dates 404. One plan is one Listing, keyed on the plan id."
    ),
)

BASE = "https://feverup.com"
CITY_URL = f"{BASE}/de/wien"
MAX_PLANS = 60

_PLAN_HREF = re.compile(r"^/m/(\d+)$")


def plan_ids(html: str) -> list[str]:
    """Every plan id the city page names, from its config and its own links.

    Read from both because they disagree: the config lists plans the rendered
    page has not got to, and the page links plans the config leaves out.
    """
    ids: list[str] = []
    seen: set[str] = set()

    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/json"}):
        text = script.string or ""
        if "cityPlansIds" not in text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        for plan_id in (data.get("page-config") or {}).get("cityPlansIds") or []:
            key = str(plan_id)
            if key not in seen:
                seen.add(key)
                ids.append(key)

    for anchor in soup.select("a[href]"):
        found = _PLAN_HREF.match(anchor["href"].split("?")[0])
        if found and found.group(1) not in seen:
            seen.add(found.group(1))
            ids.append(found.group(1))
    return ids


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    resp = ctx.http.get(CITY_URL)
    yield RawPayload(url=resp.url, body=resp.body, kind="city")

    for plan_id in plan_ids(resp.text)[:MAX_PLANS]:
        try:
            plan = ctx.http.get(f"{BASE}/m/{plan_id}")
        except FetchError:
            # Plans get delisted; one gone page must not end the crawl.
            continue
        yield RawPayload(
            url=plan.url, body=plan.body, kind="plan", meta={"plan_id": plan_id}
        )


def parse(payload: RawPayload) -> Iterator[RawListing]:
    # The city page exists to discover plan ids and as a structural canary.
    if payload.kind != "plan":
        return

    plan_id = payload.meta.get("plan_id")
    seen: set[str] = set()

    for node in jsonld.events_in(payload.text):
        start = parse_iso_when(node.get("startDate"))
        if start is None:
            continue

        source_ref = str(plan_id or node.get("url") or "")
        if not source_ref or source_ref in seen:
            continue
        seen.add(source_ref)

        where = jsonld.place(node)
        latitude, longitude = jsonld.coordinates(node)
        value, currency = jsonld.price(node)
        url = node.get("url") or payload.url

        yield RawListing(
            source_ref=source_ref,
            occurrences=[
                RawOccurrence(
                    start=start,
                    end=paired_end(start, parse_iso_when(node.get("endDate"))),
                )
            ],
            url=url,
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
            price_min=value,
            price_currency=currency,
            ticket_url=url,
            image_url=jsonld.image(node),
        )
