import json
from collections.abc import Iterator
from typing import Any

from bs4 import BeautifulSoup


def first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def mapping(value: Any) -> dict[str, Any]:
    found = first(value)
    return found if isinstance(found, dict) else {}


def place(node: Any) -> dict[str, str | None]:
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
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coordinates(node: Any) -> tuple[float | None, float | None]:
    location = mapping(node.get("location") if isinstance(node, dict) else None)
    geo = mapping(location.get("geo"))
    return _number(geo.get("latitude")), _number(geo.get("longitude"))


def price(node: Any) -> tuple[float | None, str | None]:
    offer = mapping(node.get("offers") if isinstance(node, dict) else None)
    if not offer:
        return None, None
    return _number(offer.get("price") or offer.get("lowPrice")), offer.get(
        "priceCurrency"
    )


def image(node: Any) -> str | None:
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
    soup = BeautifulSoup(html, "lxml")
    found: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "{}")
        except json.JSONDecodeError:
            continue
        found.extend(_walk(data))
    return found
