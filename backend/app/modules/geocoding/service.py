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
            first = first or found
            continue
        return found, True

    if first is None:
        return None, answered
    return first.model_copy(update={"precision": "approx"}), True


def locate(db: Session, address: Address, geocoder: Geocoder) -> Located | None:
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
