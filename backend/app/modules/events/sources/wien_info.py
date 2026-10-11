import datetime as dt
import json
from collections.abc import Iterator
from typing import Any

from ..scraped import RawListing, RawOccurrence
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="wien_info",
    locales=("en",),
    delay_seconds=1.0,
    notes=(
        "One call returns everything; no date params exist (the hash params are "
        "client-side). Items carry EITHER dates[] (explicit, possibly gapped) OR "
        "startDate/endDate (a span) - must handle both. IDs DIFFER PER LOCALE, so "
        "only one locale is fetched (see module docstring)."
    ),
)

ENDPOINT = "https://www.wien.info/ajax/{locale}/events"
BASE = "https://www.wien.info"


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    for locale in ctx.locales:
        resp = ctx.http.get(
            ENDPOINT.format(locale=locale),
            headers={
                "Accept": "application/json",
                "Referer": f"{BASE}/{locale}/now-on/events",
            },
        )
        yield RawPayload(
            url=resp.url,
            body=resp.body,
            kind=f"events-{locale}",
            meta={"locale": locale},
        )


def _occurrences(item: dict[str, Any]) -> list[RawOccurrence]:
    out: list[RawOccurrence] = []

    explicit = item.get("dates") or []
    if explicit:
        for value in explicit:
            try:
                out.append(RawOccurrence(start=dt.date.fromisoformat(str(value)[:10])))
            except ValueError:
                continue
        return out

    start_raw = item.get("startDate")
    end_raw = item.get("endDate") or start_raw
    if not start_raw:
        return out
    try:
        start = dt.date.fromisoformat(str(start_raw)[:10])
        end = dt.date.fromisoformat(str(end_raw)[:10])
    except ValueError:
        return out
    out.append(RawOccurrence(start=start, end=end))
    return out


def parse(payload: RawPayload) -> Iterator[RawListing]:
    data = json.loads(payload.text)

    for item in data.get("items") or []:
        item_id = item.get("id")
        if not item_id:
            continue

        occurrences = _occurrences(item)
        if not occurrences:
            continue

        url = item.get("url") or ""
        if url.startswith("/"):
            url = BASE + url

        subtitle = item.get("subtitle")
        category = item.get("category")

        yield RawListing(
            source_ref=str(item_id),
            occurrences=occurrences,
            url=url or None,
            title=item.get("title"),
            description=subtitle,
            lang="en",
            venue_name=item.get("location"),
            city="Wien",
            country="AT",
            categories_raw=[category] if category else [],
            image_url=item.get("imageUrl"),
        )
