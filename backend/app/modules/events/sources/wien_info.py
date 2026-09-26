"""wien.info - the official Vienna tourism calendar. The largest single Source.

One unparameterised request returns the entire calendar (~841 Listings, ~74 of
them on any given Saturday). The endpoint was found by walking the site's own
bundle graph: `/assets/modern/core.js` names `./modules/eventsearch.ts`, whose real
implementation lives in `filter-app` -> `apps-*.js`, which resolves
`./configs/events/events.config.ts` -> `events.config-*.js`, and *that* file
declares `endpoints: {de: "/ajax/de/events", en: "/ajax/en/events"}`.

The `dr`/`f1` parameters in the site's URL hash are **client-side only** - the
app downloads everything and filters in the browser, so there is nothing to pass.

**Single locale only - English.** The original plan was to fetch both (one
request each) and merge on the item `id`, but wien.info assigns *different ids
per locale*: the same happening is `1002218` in German and `1002236` in English.
No reliable cross-locale key exists either - the best candidate, `tags + dates`,
matches only 51% of items unambiguously, and `imageUrl` just 21%. Fetching both
would therefore create ~840 duplicate Listings for deduplication to clean up,
which is precisely what the "two locales must be one Listing" rule exists to
prevent. German titles for this Source are a follow-up.
"""

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
    """Build occurrences from either the explicit list or the span.

    `dates` is authoritative when present and may be **gapped** - the 14th,
    15th, 16th and then the 20th - so collapsing it into a single span would
    falsely claim the happening runs on the days in between.

    wien.info gives no clock times in the listing payload, so every Occurrence is
    a plain `date`, which is all-day downstream. Times would need one detail
    fetch per item, and there are 841 of them.
    """
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
            source_event_id=str(item_id),
            occurrences=occurrences,
            url=url or None,
            title=item.get("title"),
            description=subtitle,
            # English, always: fetching the German locale would double the
            # catalogue rather than translate it - see the module docstring.
            lang="en",
            venue_name=item.get("location"),
            city="Wien",
            country="AT",
            categories_raw=[category] if category else [],
            image_url=item.get("imageUrl"),
        )
