import json
from typing import Any

from pydantic import BaseModel, ConfigDict

LAT_MIN, LAT_MAX = 46.0, 49.5
LON_MIN, LON_MAX = 9.0, 17.5

VIENNA = (48.2083, 16.3731)

COARSE = frozenset({"city", "county", "state", "country", "continent", "other"})

_PRECISION = {
    "house": "exact",
    "street": "street",
    "district": "city",
    "locality": "city",
}


class Found(BaseModel):
    model_config = ConfigDict(frozen=True)

    latitude: float
    longitude: float
    precision: str
    postcode: str | None = None
    raw: str | None = None


def in_austria(latitude: float, longitude: float) -> bool:
    return LAT_MIN <= latitude <= LAT_MAX and LON_MIN <= longitude <= LON_MAX


def read(payload: Any) -> Found | None:
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
