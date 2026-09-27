"""Reading a Photon answer, and deciding whether to believe it.

Photon answers *every* query, and the answer can be nonsense: ask it for "Wien,
Austria" and it returns an office building typed `house` before it returns the
city. So a result is checked rather than parsed - it has to be somewhere in
Austria, and it has to be specific enough to be worth having.

Values in, values out: this is the file to reproduce a wrong position against,
with the exact payload the geocoder returned.
"""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

# Austria, generously. A result outside this is a same-named place somewhere
# else, which Photon returns happily.
LAT_MIN, LAT_MAX = 46.0, 49.5
LON_MIN, LON_MAX = 9.0, 17.5

# Vienna, to bias the search without excluding the rest of the country.
VIENNA = (48.2083, 16.3731)

# A match this coarse is worse than no match at all: it puts every unresolved
# Listing on one pin, which breaks "what is on near here" *and* gives
# deduplication a distance of zero between unrelated happenings. It is the
# city-centroid problem that goabase's parse already refuses, arriving by
# another route.
COARSE = frozenset({"city", "county", "state", "country", "continent", "other"})

_PRECISION = {
    "house": "exact",
    "street": "street",
    "district": "city",
    "locality": "city",
}


class Found(BaseModel):
    """A position Photon returned and this module was willing to believe."""

    model_config = ConfigDict(frozen=True)

    latitude: float
    longitude: float
    precision: str
    # Photon's own, not ours. Used to catch a *confident* wrong answer: a church
    # named with its parish resolves to a different church in another district.
    postcode: str | None = None
    raw: str | None = None


def in_austria(latitude: float, longitude: float) -> bool:
    return LAT_MIN <= latitude <= LAT_MAX and LON_MIN <= longitude <= LON_MAX


def read(payload: Any) -> Found | None:
    """The first believable feature of a Photon response, or None."""
    features = (payload or {}).get("features") or []
    if not features:
        return None

    feature = features[0]
    properties = feature.get("properties") or {}
    coordinates = (feature.get("geometry") or {}).get("coordinates") or []
    if len(coordinates) < 2:
        return None
    try:
        longitude, latitude = float(coordinates[0]), float(coordinates[1])
    except (TypeError, ValueError):
        return None

    kind = str(properties.get("type") or "").lower()
    if kind in COARSE or not in_austria(latitude, longitude):
        return None

    return Found(
        latitude=latitude,
        longitude=longitude,
        precision=_PRECISION.get(kind, "approx"),
        postcode=str(properties.get("postcode") or "") or None,
        raw=json.dumps(properties, ensure_ascii=False)[:2000],
    )
