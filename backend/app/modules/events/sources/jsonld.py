"""Reading the schema.org data a page embeds about itself.

Nine Sources publish JSON-LD and each needs the same handful of things from it -
where it happens, what it costs, which picture is its own - which is why those
live here rather than in each of them.

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


def _number(value: Any) -> float | None:
    """schema.org writes numbers as strings about as often as numbers."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coordinates(node: Any) -> tuple[float | None, float | None]:
    """The Place's coordinates, when it published a usable pair.

    Half the coordinates in these feeds arrive quoted (`"48.2006"`), so reading
    them with `float` rather than trusting the JSON type is not defensive
    clutter - it is the difference between a Listing that needs geocoding and
    one that does not.
    """
    location = mapping(node.get("location") if isinstance(node, dict) else None)
    geo = mapping(location.get("geo"))
    return _number(geo.get("latitude")), _number(geo.get("longitude"))


def price(node: Any) -> tuple[float | None, str | None]:
    """What the first Offer asks, and in which currency.

    `lowPrice` is the same claim as `price` for a ticket sold at one tier, and
    a site uses whichever it feels like, sometimes on neighbouring events.

    What a zero *means* is left to the caller, because Sources disagree: this
    one reads it as a published price of nothing, while rausgegangen reads its
    own `0.00` as "no price given" and so does not come through here at all.
    """
    offer = mapping(node.get("offers") if isinstance(node, dict) else None)
    if not offer:
        return None, None
    return _number(offer.get("price") or offer.get("lowPrice")), offer.get(
        "priceCurrency"
    )


def image(node: Any) -> str | None:
    """The single image URL, when the property held one at all.

    JSON-LD lets `image` be a string, a list, or an `ImageObject`; the last is
    not a URL and a Source that wants a picture has to look elsewhere for one.
    """
    found = first(node.get("image") if isinstance(node, dict) else None)
    return found if isinstance(found, str) else None


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
