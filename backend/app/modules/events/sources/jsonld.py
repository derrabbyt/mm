"""Reading the schema.org data a page embeds about itself.

Three Sources publish JSON-LD and each needs the same handful of things from it,
which is why those live here rather than in each of them.

`events_in` walks the whole document rather than reading a known path, because
the shape differs per site: one nests its Event inside an `@graph`, another
inside a `WebPage`, a third emits a bare list of them. Walking costs nothing at
these sizes and survives a reshuffle upstream.

`first` exists because JSON-LD lets any property be a single object or a list of
them, interchangeably. A site is usually consistent until it is not - one that
emits a single `offers` object will emit two the day an event gets a second
ticket tier - so every read of a property that *may* be a list goes through it.

Note that "Event" here is schema.org's word. One such node is one Listing to us;
whether two of them are the same real-world Event is decided much later.
"""

import json
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup


def first(value: Any) -> Any:
    """The single value to read, whether the property held one or a list."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def as_list(value: Any) -> list[Any]:
    """The property as a list, whether it held one value or several.

    The inverse of `first`, for a property a Source reads all of - a line-up of
    performers rather than the one offer that sets the price.
    """
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def mapping(value: Any) -> dict[str, Any]:
    """`first(value)` when it is an object, else an empty one.

    Saves every caller an `isinstance` before reaching for a key: a missing
    `location` and a `location` that arrived as a string both read as absent.
    """
    found = first(value)
    return found if isinstance(found, dict) else {}


def place(node: Any) -> dict[str, str | None]:
    """Where an Event node says it happens, as far as schema.org describes it.

    `location` may be an object or a list of them, and its `address` the same, so
    both go through `mapping`. The names are schema.org's: what to call these
    fields in a Listing, and what to fall back on when a Source leaves the city
    out, is the Source's business rather than this module's.
    """
    location = mapping(node.get("location") if isinstance(node, dict) else None)
    address = mapping(location.get("address"))
    return {
        "venue_name": location.get("name"),
        "street": address.get("streetAddress"),
        "postcode": address.get("postalCode"),
        "city": address.get("addressLocality"),
        "country": address.get("addressCountry"),
    }


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if str(node.get("@type", "")).endswith("Event"):
            yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


def events_in(html: str) -> list[dict[str, Any]]:
    """Every schema.org Event node embedded in this page.

    A script whose JSON will not parse is skipped rather than failing the page:
    sites embed several blocks and one of them being broken is common.
    """
    soup = BeautifulSoup(html, "lxml")
    found: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "{}")
        except json.JSONDecodeError:
            continue
        found.extend(_walk(data))
    return found
