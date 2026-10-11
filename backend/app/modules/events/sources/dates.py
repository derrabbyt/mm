import datetime as dt
import re
from typing import Any
from zoneinfo import ZoneInfo

from ..scraped import VIENNA_TZ

TZ = ZoneInfo(VIENNA_TZ)


def parse_iso_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()

    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"

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
    if isinstance(moment, dt.datetime) and moment.hour == 0 and moment.minute == 0:
        return moment.date()
    return moment


def paired_end(
    start: dt.datetime | dt.date | None, end: dt.datetime | dt.date | None
) -> dt.datetime | dt.date | None:
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
    for length in (4, 3):
        if len(key) >= length and key[:length] in _MONTHS:
            return _MONTHS[key[:length]]
    return None


def infer_year(month: int, day: int, reference: dt.date) -> int:
    year = reference.year
    try:
        candidate = dt.date(year, month, day)
    except ValueError:
        return year
    if (reference - candidate).days > 31:
        return year + 1
    return year


def parse_date(text: str, reference: dt.date | None = None) -> dt.date | None:
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
    if date is None:
        return None
    return dt.datetime.combine(date, time) if time else date
