"""Turning an address into the questions worth asking a geocoder.

An `Address` is asked for in several forms, most specific first, because which
one works depends on what the Source gave. The venue name *with* the street is
the best single question: for one street, the street alone resolves to a
different club in the same building, while "Das Werk, Spittelauer Laende 12"
resolves to the right one.

Asking several questions per address is only reasonable against a self-hosted
geocoder, where a lookup costs nothing. Pointed at a public endpoint this would
multiply requests on exactly the addresses that are hardest to resolve.

The cache key comes from the *first* variant only, so one address costs one cache
entry however many questions end up being asked.
"""

import re
import unicodedata

from ...core.contracts import Address

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)

# A venue name often carries a qualifier that misleads a fuzzy match. One church
# named with its parish lands on a different church 7.7 km away; the bare name is
# exact. Splitting here lets the chain retry without the qualifier.
_QUALIFIER = re.compile(r"\s+[-–—]\s+|\s*[(\[]")


def cache_key(query: str) -> str | None:
    """Casefolded, accent-stripped, punctuation-free, so two spellings of one
    address share a cache entry."""
    if not query:
        return None
    text = unicodedata.normalize("NFKD", query)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PUNCTUATION.sub(" ", text).casefold()
    return _WHITESPACE.sub("", text) or None


def _without_qualifier(venue: str) -> str:
    return _QUALIFIER.split(venue, maxsplit=1)[0].strip()


def variants(address: Address) -> list[str]:
    """Every question worth asking for this address, most specific first."""
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
