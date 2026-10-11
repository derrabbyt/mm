import csv
import io
import logging
import zipfile
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...core.dataset import DEPARTURES, DayClass, Departure

logger = logging.getLogger(__name__)

WEEKDAY_COLUMNS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

SERVICE_ADDED = "1"
SERVICE_REMOVED = "2"

DATE_FORMAT = "%Y%m%d"


class FeedError(Exception):
    """A transit feed the bake cannot derive a reference week from."""


@dataclass(frozen=True)
class _Service:
    service_id: str
    runs_on: frozenset[int]
    start: date
    end: date


@dataclass(frozen=True)
class ServiceCalendar:
    window: tuple[date, date]
    timezone: ZoneInfo
    services: tuple[_Service, ...]
    exceptions: Mapping[date, tuple[tuple[str, str], ...]]

    def active_services(self, day: date) -> frozenset[str]:
        running = {
            service.service_id
            for service in self.services
            if service.start <= day <= service.end and day.weekday() in service.runs_on
        }
        for service_id, exception in self.exceptions.get(day, ()):
            if exception == SERVICE_ADDED:
                running.add(service_id)
            elif exception == SERVICE_REMOVED:
                running.discard(service_id)
        return frozenset(running)

    def days(self, weekday: int) -> list[date]:
        start, end = self.window
        first = start + timedelta(days=(weekday - start.weekday()) % 7)
        weeks = (end - first).days // 7 + 1
        return [first + timedelta(days=7 * n) for n in range(max(weeks, 0))]


@dataclass(frozen=True)
class ReferenceWeek:
    dates: Mapping[DayClass, date]

    def moment(self, departure: Departure, timezone: ZoneInfo) -> datetime:
        reference = self.dates[departure.day]
        return datetime(
            reference.year,
            reference.month,
            reference.day,
            departure.hour,
            tzinfo=timezone,
        )


def _rows(archive: zipfile.ZipFile, name: str) -> Iterable[dict[str, str]]:
    with archive.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


def _date(stamp: str) -> date:
    try:
        return datetime.strptime(stamp.strip(), DATE_FORMAT).date()  # noqa: DTZ007
    except ValueError as error:
        raise FeedError(f"{stamp!r} is not a GTFS date") from error


def read_service_calendar(gtfs_zip: Path) -> ServiceCalendar:
    try:
        archive = zipfile.ZipFile(gtfs_zip)
    except (OSError, zipfile.BadZipFile) as error:
        raise FeedError(f"cannot read the transit feed {gtfs_zip}: {error}") from error

    with archive:
        names = set(archive.namelist())

        services: list[_Service] = []
        if "calendar.txt" in names:
            for row in _rows(archive, "calendar.txt"):
                services.append(
                    _Service(
                        service_id=row["service_id"].strip('"'),
                        runs_on=frozenset(
                            index
                            for index, column in enumerate(WEEKDAY_COLUMNS)
                            if row[column].strip('"') == "1"
                        ),
                        start=_date(row["start_date"].strip('"')),
                        end=_date(row["end_date"].strip('"')),
                    )
                )

        exceptions: dict[date, list[tuple[str, str]]] = {}
        if "calendar_dates.txt" in names:
            for row in _rows(archive, "calendar_dates.txt"):
                day = _date(row["date"].strip('"'))
                exceptions.setdefault(day, []).append(
                    (
                        row["service_id"].strip('"'),
                        row["exception_type"].strip('"'),
                    )
                )

        if not services and not exceptions:
            raise FeedError(
                f"{gtfs_zip.name} has neither calendar.txt nor "
                "calendar_dates.txt, so there is no validity window to derive "
                "Departures from"
            )

        timezone = _timezone(archive, names)

    starts = [service.start for service in services] + list(exceptions)
    ends = [service.end for service in services] + list(exceptions)
    window = (min(starts), max(ends))
    logger.info(
        "transit feed %s is valid %s..%s (%s)",
        gtfs_zip.name,
        window[0],
        window[1],
        timezone,
    )
    return ServiceCalendar(
        window=window,
        timezone=timezone,
        services=tuple(services),
        exceptions={day: tuple(found) for day, found in exceptions.items()},
    )


def _timezone(archive: zipfile.ZipFile, names: set[str]) -> ZoneInfo:
    if "agency.txt" not in names:
        raise FeedError("the transit feed has no agency.txt, so no timezone")
    for row in _rows(archive, "agency.txt"):
        named = row.get("agency_timezone", "").strip('"').strip()
        if named:
            try:
                return ZoneInfo(named)
            except ZoneInfoNotFoundError as error:
                raise FeedError(f"unknown agency_timezone {named!r}") from error
    raise FeedError("the transit feed's agency.txt names no timezone")


def reference_week(calendar: ServiceCalendar) -> ReferenceWeek:
    start, end = calendar.window
    middle = start + (end - start) / 2

    chosen: dict[DayClass, date] = {}
    for day_class in DayClass:
        candidates = calendar.days(day_class.weekday)
        patterns = Counter(
            calendar.active_services(day)
            for day in candidates
            if calendar.active_services(day)
        )
        if not patterns:
            raise FeedError(
                f"the feed runs no service at all on any {day_class.value} "
                f"between {start} and {end}"
            )
        ordinary, _ = patterns.most_common(1)[0]
        chosen[day_class] = min(
            (day for day in candidates if calendar.active_services(day) == ordinary),
            key=lambda day: (abs(day - middle), day),
        )

    saturday, sunday = chosen[DayClass.SATURDAY], chosen[DayClass.SUNDAY]
    if calendar.active_services(saturday) == calendar.active_services(sunday):
        raise FeedError(
            f"the feed runs the same services on Saturday {saturday} as on "
            f"Sunday {sunday}, so a Saturday matrix could only come out as its "
            "Sunday one"
        )

    for day_class, day in chosen.items():
        logger.info(
            "reference %s: %s (%d services)",
            day_class.value,
            day,
            len(calendar.active_services(day)),
        )
    return ReferenceWeek(dates=chosen)


def departures_for(
    week: ReferenceWeek, calendar: ServiceCalendar
) -> dict[str, datetime]:
    moments = {
        departure.matrix_key: week.moment(departure, calendar.timezone)
        for departure in DEPARTURES
    }
    start, end = calendar.window
    outside = sorted(
        key for key, moment in moments.items() if not start <= moment.date() <= end
    )
    if outside:
        raise FeedError(
            f"{', '.join(outside)} fall outside the feed's validity window "
            f"{start}..{end}"
        )
    return moments
