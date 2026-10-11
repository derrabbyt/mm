import re

LAT_MIN, LAT_MAX = 48.10, 48.34
LON_MIN, LON_MAX = 16.17, 16.59

POSTCODE = re.compile(r"^1[0-2]\d{2}$")

_VIENNA_NAMES = {"wien", "vienna", "wien - innere stadt", "wienna"}


def is_vienna(
    lat: float | None = None,
    lon: float | None = None,
    postcode: str | None = None,
    city: str | None = None,
) -> bool:
    if lat is not None and lon is not None:
        return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX

    if postcode:
        digits = str(postcode).strip()
        if digits.isdigit() and len(digits) == 4:
            return bool(POSTCODE.match(digits))

    if city:
        name = str(city).strip().casefold()
        if name:
            return name in _VIENNA_NAMES or name.startswith("wien")

    return True
