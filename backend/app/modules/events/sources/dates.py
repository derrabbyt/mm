"""Reading the date formats Sources actually emit.

Only what a Source here needs. The German listing-page parsing that the
HTML Sources will want - a year-less `Mi, 12. Aug`, and inferring which year a
month without one means - lands with the first Source that needs it.
"""

import datetime as dt
from typing import Any


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    """An ISO timestamp, tolerating the two shapes Sources get wrong.

    `fromisoformat` will not take a `+0200` offset written without its colon,
    which one Source emits, and another appends fractional seconds it then
    truncates inconsistently. Both are salvaged rather than dropped: retrying on
    the first nineteen characters gives up the offset but keeps the wall clock,
    which normalisation then reads as Vienna local.

    Returns None for anything else, so a Source decides whether a date it cannot
    read means "skip this record" or "this happening has no date".
    """
    if not value:
        return None
    text = str(value).strip()

    # +HHMM -> +HH:MM
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"

    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            return dt.datetime.fromisoformat(text[:19])
        except ValueError:
            return None
