"""Reading the date formats Sources actually emit.

Two jobs. The ISO group - `parse_iso_datetime`, `parse_iso_when`,
`day_if_midnight` and `paired_end` - salvages the timestamps the JSON and
JSON-LD Sources get subtly wrong, and decides when one of them means a day
rather than a clock time. The rest reads a German listing page's idea of a
date: these pages spell one several ways and frequently leave the year out
(`15. August`, `Mi, 12. Aug`), which makes naive parsing produce a Listing in the
wrong year, or none at all. Centralised so that each Source's parse stays about
the page's *structure* rather than about calendars.

Values in, values out.
"""

import datetime as dt
import re
from typing import Any
from zoneinfo import ZoneInfo

from ..scraped import VIENNA_TZ

TZ = ZoneInfo(VIENNA_TZ)


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    """An ISO timestamp, tolerating the shapes Sources get wrong.

    `fromisoformat` will not take a `+0200` offset written without its colon,
    which one Source emits, nor a trailing `Z`, which two others do. Both are
    salvaged rather than dropped: retrying on a truncated prefix gives up the
    offset but keeps the wall clock, which normalisation reads as Vienna local.

    Returns None for anything else, so a Source decides whether a date it cannot
    read means "skip this record" or "this happening has no date".
    """
    if not value:
        return None
    text = str(value).strip()

    # A *trailing* Z only. Replacing every Z would rewrite one inside a value
    # that merely contains the letter.
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"

    # +HHMM -> +HH:MM
    if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"

    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        pass
    for cut in (19, 16):
        try:
            return dt.datetime.fromisoformat(text[:cut])
        except ValueError:
            continue
    return None


def parse_iso_when(value: Any) -> dt.datetime | dt.date | None:
    """An ISO value read as the *kind* of moment it actually describes.

    A bare `YYYY-MM-DD` is a day, not a midnight. `parse_iso_datetime` would
    hand back 00:00 for it, which scraped.py warns about specifically: a
    fabricated midnight start sorts every time-unknown Listing above every real
    evening one. Four Sources emit both shapes in the same field, so the
    distinction is drawn here rather than four times over.
    """
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 10:
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            return None
    parsed = parse_iso_datetime(text)
    if parsed is not None:
        return parsed
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def day_if_midnight(
    moment: dt.datetime | dt.date | None,
) -> dt.datetime | dt.date | None:
    """Read an exact 00:00 as the day it names rather than as a start time.

    Two Sources publish midnight to mean "on this day": the City of Vienna does
    it across whole subEvent series, and eventjet does it for day-level entries.
    Neither means a happening that begins at midnight, and a real one listed at
    exactly 00:00.00 is rare enough that losing its clock time is the cheaper
    mistake.
    """
    if isinstance(moment, dt.datetime) and moment.hour == 0 and moment.minute == 0:
        return moment.date()
    return moment


def paired_end(
    start: dt.datetime | dt.date | None, end: dt.datetime | dt.date | None
) -> dt.datetime | dt.date | None:
    """The end, but only when it is the same kind of value as the start.

    A `date` start with a `datetime` end says two contradictory things about
    whether the time is known, and normalisation has no way to reconcile them.
    Dropping the end keeps the Occurrence honest: the day is certain, the
    finish is not.
    """
    if end is None:
        return None
    if isinstance(start, dt.datetime) != isinstance(end, dt.datetime):
        return None
    return end


_MONTHS: dict[str, int] = {}
for _names, _num in [
    (("jan", "jänner", "januar", "january"), 1),
    (("feb", "februar", "february"), 2),
    (("mär", "maer", "märz", "maerz", "mar", "march"), 3),
    (("apr", "april"), 4),
    (("mai", "may"), 5),
    (("jun", "juni", "june"), 6),
    (("jul", "juli", "july"), 7),
    (("aug", "august"), 8),
    (("sep", "sept", "september"), 9),
    (("okt", "oct", "oktober", "october"), 10),
    (("nov", "november"), 11),
    (("dez", "dec", "dezember", "december"), 12),
]:
    for _name in _names:
        _MONTHS[_name] = _num

# "15. August 2026" / "12. Aug" / "Mi, 12. Aug" / "15 August 2026"
_DAY_MONTH = re.compile(
    r"\b(\d{1,2})\.?\s*([A-Za-zÄÖÜäöüß]{3,10})\.?\s*(\d{4})?", re.UNICODE
)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DOTTED = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_TIME = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")


def month_number(name: str) -> int | None:
    key = name.strip().rstrip(".").casefold()
    if key in _MONTHS:
        return _MONTHS[key]
    # Try progressively shorter prefixes: "Septem" -> "sept" -> "sep".
    for length in (4, 3):
        if len(key) >= length and key[:length] in _MONTHS:
            return _MONTHS[key[:length]]
    return None


def infer_year(month: int, day: int, reference: dt.date) -> int:
    """Pick the year for a date given without one.

    A listing page shows what is upcoming, so a month earlier than the
    reference month means next year. Without this, a January date read in
    December lands eleven months in the past and is quarantined as
    out-of-window.
    """
    year = reference.year
    try:
        candidate = dt.date(year, month, day)
    except ValueError:  # e.g. 29 Feb in a non-leap year
        return year
    # More than a month in the past => the source means next year.
    if (reference - candidate).days > 31:
        return year + 1
    return year


def parse_date(text: str, reference: dt.date | None = None) -> dt.date | None:
    """Find the first date in `text`, inferring a missing year.

    `reference` is what a year-less date resolves against. A Source passes the
    day its payload was fetched, which is what keeps its parse a pure function of
    that payload; the fallback, for a caller with nothing to offer, is Vienna's
    today rather than the host's.
    """
    if not text:
        return None
    reference = reference or dt.datetime.now(TZ).date()

    iso = _ISO.search(text)
    if iso:
        try:
            return dt.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            pass

    dotted = _DOTTED.search(text)
    if dotted:
        try:
            return dt.date(
                int(dotted.group(3)), int(dotted.group(2)), int(dotted.group(1))
            )
        except ValueError:
            pass

    for match in _DAY_MONTH.finditer(text):
        day = int(match.group(1))
        month = month_number(match.group(2))
        if month is None or not 1 <= day <= 31:
            continue
        year = (
            int(match.group(3)) if match.group(3) else infer_year(month, day, reference)
        )
        try:
            return dt.date(year, month, day)
        except ValueError:
            continue
    return None


def parse_time(text: str) -> dt.time | None:
    """Find the first clock time in `text`."""
    if not text:
        return None
    match = _TIME.search(text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return dt.time(hour, minute)


def combine(date: dt.date | None, time: dt.time | None) -> dt.datetime | dt.date | None:
    """Return a datetime when the time is known, else the bare date.

    Returning a plain `date` is meaningful downstream: it marks the Occurrence
    all-day rather than fabricating a midnight start.
    """
    if date is None:
        return None
    return dt.datetime.combine(date, time) if time else date
