import datetime as dt
import json
from collections.abc import Iterator
from typing import Any

from .. import region
from ..scraped import RawListing, RawOccurrence
from .spec import FetchContext, RawPayload, SourceSpec

SPEC = SourceSpec(
    name="goodnight",
    locales=("de",),
    delay_seconds=0.5,
    notes=(
        "address is [] (an empty LIST) for one_off_location items - type-check. "
        "days has no cap; limit/page are ignored. ~5-14 a day, curated."
    ),
)

ENDPOINT = "https://goodnight.at/api/grouped-events"
BASE = "https://goodnight.at"


def fetch(ctx: FetchContext) -> Iterator[RawPayload]:
    days = (ctx.date_to - ctx.date_from).days + 1
    url = f"{ENDPOINT}?date={ctx.date_from.isoformat()}&days={max(1, days)}"
    resp = ctx.http.get(
        url, headers={"Accept": "application/json", "Referer": f"{BASE}/events"}
    )
    yield RawPayload(url=resp.url, body=resp.body, kind="grouped-events")


def _address(location: dict[str, Any]) -> dict[str, Any]:
    address = location.get("address")
    return address if isinstance(address, dict) else {}


def _time(value: str | None) -> dt.time | None:
    if not value:
        return None
    try:
        hour, minute = str(value).split(":")[:2]
        return dt.time(int(hour), int(minute))
    except (ValueError, TypeError):
        return None


def parse(payload: RawPayload) -> Iterator[RawListing]:
    data = json.loads(payload.text)

    seen: set[str] = set()

    for bucket in data.get("data") or []:
        for item in bucket.get("events") or []:
            event_id = item.get("id")
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)

            span = item.get("event_date") or {}
            start_raw = span.get("start")
            end_raw = span.get("end") or start_raw
            if not start_raw:
                continue
            try:
                start_date = dt.date.fromisoformat(str(start_raw)[:10])
                end_date = dt.date.fromisoformat(str(end_raw)[:10])
            except ValueError:
                continue

            start_time = _time(item.get("time_start"))
            end_time = _time(item.get("time_end"))

            if start_time is not None:
                start: dt.datetime | dt.date = dt.datetime.combine(
                    start_date, start_time
                )
                if end_time is not None:
                    end: dt.datetime | dt.date | None = dt.datetime.combine(
                        end_date, end_time
                    )
                else:
                    end = end_date if end_date != start_date else None
            else:
                start = start_date
                end = end_date

            location = item.get("location") or {}
            address = _address(location)
            if not region.is_vienna(
                postcode=address.get("zip_code"), city=address.get("city")
            ):
                continue
            category = (item.get("category") or {}).get("title")
            slug = item.get("slug")

            yield RawListing(
                source_ref=str(event_id),
                occurrences=[RawOccurrence(start=start, end=end)],
                url=f"{BASE}/events/{slug}" if slug else None,
                origin_url=item.get("event_link"),
                title=item.get("title"),
                description=item.get("teaser_text"),
                lang="de",
                venue_name=location.get("title") or address.get("name"),
                street=address.get("street"),
                postcode=address.get("zip_code"),
                city=address.get("city") or "Wien",
                country="AT",
                categories_raw=[category] if category else [],
            )
