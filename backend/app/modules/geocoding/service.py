"""Resolving an address to a position, asking the geocoder as little as possible.

The order is: cache, then the geocoder, then the cache again. Everything the
geocoder says is kept, including a miss, so a second run over the same corpus
costs no lookups at all.

Which answer to believe, when several questions get different ones, is the only
judgement here, and it turns on whether the address states a postcode:

* **It does, and an answer agrees with it** - that answer wins. This is what
  catches a *confident* wrong answer, the failure mode a fuzzy match produces: a
  venue name carrying its parish resolves to a different building in another
  district, with nothing to suggest anything is wrong.
* **It does, and every answer disagrees** - the first is kept anyway, because an
  approximate position still beats none, but its precision is downgraded to
  `approx` so the row is visibly less trustworthy.
* **It does not** - the first answer wins as it stands. There is nothing to
  corroborate against, so there is nothing to downgrade on either.
"""

import logging

from sqlalchemy.orm import Session

from ...core.contracts import Address, Located, Position
from . import query, repository
from .client import Geocoder, Unreachable
from .photon import Found

logger = logging.getLogger(__name__)


def _best(
    geocoder: Geocoder, asked: list[str], wanted_postcode: str | None
) -> tuple[Found | None, bool]:
    """Ask each question in turn and take the best-corroborated answer.

    Returns the answer and whether the geocoder answered *anything at all*. The
    second is what tells the caller it has learned a fact about the address,
    rather than a fact about the geocoder being down.
    """
    first: Found | None = None
    answered = False
    for question in asked:
        try:
            found = geocoder.lookup(question)
        except Unreachable:
            continue
        answered = True
        if found is None:
            continue
        if wanted_postcode and found.postcode:
            if found.postcode == wanted_postcode:
                return found, True
            # A real answer to a different place. Keep it only as a fallback.
            first = first or found
            continue
        return found, True

    if first is None:
        return None, answered
    return first.model_copy(update={"precision": "approx"}), True


def locate(db: Session, address: Address, geocoder: Geocoder) -> Located | None:
    """Where this address is, or None if the geocoder could not say.

    Answers from the cache when it can, and records what it learns when it
    cannot, so the same address is never asked for twice.
    """
    asked = query.variants(address)
    if not asked:
        return None
    key = query.cache_key(asked[0])
    if key is None:
        return None

    remembered = repository.cached(db, key)
    if remembered is not None:
        if remembered.lat is None or remembered.lon is None:
            return None
        return Located(
            position=Position(latitude=remembered.lat, longitude=remembered.lon),
            precision=remembered.precision or "approx",
        )

    found, answered = _best(geocoder, asked, (address.postcode or "").strip() or None)
    if not answered:
        # The geocoder never spoke. Remembering that would turn one outage into a
        # permanent verdict on every address it happened to cover.
        return None

    repository.remember(db, key, asked[0], found, geocoder.name)
    if found is None:
        return None
    return Located(
        position=Position(latitude=found.latitude, longitude=found.longitude),
        precision=found.precision,
    )
