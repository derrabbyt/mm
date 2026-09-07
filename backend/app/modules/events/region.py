"""Is this Listing actually in Vienna?

Several "city" listings are really **radius searches** around a coordinate, so
they return neighbouring towns and even other countries. Observed leakage:

* bandsintown - ~40% outside Vienna (Bratislava, St. Poelten, Mikulov). Handled
  in that Source via `locationText`, the only signal it provides.
* eventbrite `/d/austria--vienna/` - Bratislava, Graz, Moedling, Kunzak (CZ).

Vienna is the only region so far, so out-of-region records are dropped at parse
time rather than stored: they are not *invalid* - quarantining them would be
wrong - they are simply out of scope, and keeping them would inflate every day
count.

The checks are ordered by how much a signal can be trusted: coordinates first,
then the Austrian postcode range, then the city name.
"""

import re

# Vienna's administrative area, with a little slack for venues just outside the
# city boundary that are still functionally Vienna (e.g. the Donauinsel tip).
LAT_MIN, LAT_MAX = 48.10, 48.34
LON_MIN, LON_MAX = 16.17, 16.59

# Vienna's postcodes are 1010–1239 (11xx/12xx districts), plus 1000 as a
# placeholder some sources emit.
POSTCODE = re.compile(r"^1[0-2]\d{2}$")

_VIENNA_NAMES = {"wien", "vienna", "wien - innere stadt", "wienna"}


def is_vienna(
    lat: float | None = None,
    lon: float | None = None,
    postcode: str | None = None,
    city: str | None = None,
) -> bool:
    """True when the strongest available signal says Vienna.

    True when nothing is known: absence of evidence must not silently delete
    Listings from Sources that give no location detail at all.
    """
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
