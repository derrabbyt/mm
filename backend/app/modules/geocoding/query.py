import re
import unicodedata

from ...core.contracts import Address

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)

_QUALIFIER = re.compile(r"\s+[-–—]\s+|\s*[(\[]")


def cache_key(query: str) -> str | None:
    if not query:
        return None
    text = unicodedata.normalize("NFKD", query)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PUNCTUATION.sub(" ", text).casefold()
    return _WHITESPACE.sub("", text) or None


def _without_qualifier(venue: str) -> str:
    return _QUALIFIER.split(venue, maxsplit=1)[0].strip()


def variants(address: Address) -> list[str]:
    city = (address.city or "Wien").strip()
    venue = (address.venue_name or "").strip()
    street = (address.street or "").strip()
    postcode = (address.postcode or "").strip()

    tail = ", ".join(part for part in (postcode, city, "Austria") if part)
    asked: list[str] = []
    if venue and street:
        asked.append(f"{venue}, {street}, {tail}")
    if street:
        asked.append(f"{street}, {tail}")
    if venue:
        asked.append(f"{venue}, {city}, Austria")
        bare = _without_qualifier(venue)
        if bare and bare.casefold() != venue.casefold():
            asked.append(f"{bare}, {city}, Austria")

    ordered: list[str] = []
    seen: set[str] = set()
    for query in asked:
        folded = query.casefold()
        if folded not in seen:
            seen.add(folded)
            ordered.append(query)
    return ordered
