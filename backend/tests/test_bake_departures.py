import csv
import io
import zipfile
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.core.dataset import DEPARTURES, DayClass
from app.modules.matrix.feed import (
    FeedError,
    departures_for,
    read_service_calendar,
    reference_week,
)

WEEK = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _csv(fieldnames: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def service(name: str, days: str, start: str, end: str) -> dict:
    running = {day for day in days.split()}
    row = {"service_id": name, "start_date": start, "end_date": end}
    row.update({day: "1" if day in running else "0" for day in WEEK})
    return row


def write_feed(
    folder: Path,
    *,
    services: list[dict],
    exceptions: list[dict] | None = None,
    timezone: str = "Europe/Vienna",
    name: str = "feed.zip",
) -> Path:
    path = folder / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "agency.txt",
            _csv(
                ["agency_id", "agency_name", "agency_timezone"],
                [
                    {
                        "agency_id": "1",
                        "agency_name": "Test",
                        "agency_timezone": timezone,
                    }
                ],
            ),
        )
        if services:
            archive.writestr(
                "calendar.txt",
                _csv(["service_id", *WEEK, "start_date", "end_date"], services),
            )
        archive.writestr(
            "calendar_dates.txt",
            _csv(["service_id", "date", "exception_type"], exceptions or []),
        )
    return path


def ordinary_feed(folder: Path, *, year: int = 2026, **kwargs) -> Path:
    start, end = f"{year}0302", f"{year}1227"
    return write_feed(
        folder,
        services=[
            service("WD", "monday tuesday wednesday thursday friday", start, end),
            service("SA", "saturday", start, end),
            service("SU", "sunday", start, end),
        ],
        **kwargs,
    )


def test_the_validity_window_comes_off_the_calendar(tmp_path):
    calendar = read_service_calendar(ordinary_feed(tmp_path))
    assert calendar.window == (date(2026, 3, 2), date(2026, 12, 27))


def test_the_timezone_comes_off_the_agency(tmp_path):
    calendar = read_service_calendar(ordinary_feed(tmp_path, timezone="Europe/Lisbon"))
    assert calendar.timezone == ZoneInfo("Europe/Lisbon")


def test_a_feed_with_no_service_calendar_at_all_is_refused(tmp_path):
    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("stops.txt", "stop_id\n")
    with pytest.raises(FeedError, match="calendar"):
        read_service_calendar(path)


def test_a_feed_that_lists_every_day_separately_is_read_too(tmp_path):
    exceptions = []
    day = date(2026, 4, 1)
    while day <= date(2026, 6, 30):
        service_id = {5: "SA", 6: "SU"}.get(day.weekday(), "WD")
        exceptions.append(
            {
                "service_id": service_id,
                "date": day.strftime("%Y%m%d"),
                "exception_type": "1",
            }
        )
        day += timedelta(days=1)
    path = write_feed(tmp_path, services=[], exceptions=exceptions)

    calendar = read_service_calendar(path)
    assert calendar.window == (date(2026, 4, 1), date(2026, 6, 30))

    week = reference_week(calendar)
    assert calendar.active_services(week.dates[DayClass.SATURDAY]) == frozenset({"SA"})
    for moment in departures_for(week, calendar).values():
        assert calendar.window[0] <= moment.date() <= calendar.window[1]


def test_each_day_class_gets_a_date_the_feed_covers(tmp_path):
    calendar = read_service_calendar(ordinary_feed(tmp_path))
    week = reference_week(calendar)

    for day_class in DayClass:
        reference = week.dates[day_class]
        assert reference.weekday() == day_class.weekday
        assert calendar.window[0] <= reference <= calendar.window[1]


def test_no_date_is_written_in_code_so_another_year_needs_no_edit(tmp_path):
    week = reference_week(read_service_calendar(ordinary_feed(tmp_path, year=2036)))
    assert {reference.year for reference in week.dates.values()} == {2036}


def test_the_saturday_is_a_day_the_feed_runs_saturday_service_on(tmp_path):
    calendar = read_service_calendar(ordinary_feed(tmp_path))
    week = reference_week(calendar)

    saturday = week.dates[DayClass.SATURDAY]
    sunday = week.dates[DayClass.SUNDAY]
    assert calendar.active_services(saturday) == frozenset({"SA"})
    assert calendar.active_services(sunday) == frozenset({"SU"})


def test_a_saturday_running_sunday_service_is_not_the_one_picked(tmp_path):
    holiday = date(2026, 5, 2)
    stamp = holiday.strftime("%Y%m%d")
    path = ordinary_feed(
        tmp_path,
        exceptions=[
            {"service_id": "SA", "date": stamp, "exception_type": "2"},
            {"service_id": "SU", "date": stamp, "exception_type": "1"},
        ],
    )
    calendar = read_service_calendar(path)

    assert calendar.active_services(holiday) == frozenset({"SU"})
    assert reference_week(calendar).dates[DayClass.SATURDAY] != holiday


def test_a_feed_that_runs_one_weekend_timetable_is_refused(tmp_path):
    path = write_feed(
        tmp_path,
        services=[
            service(
                "WD", "monday tuesday wednesday thursday friday", "20260302", "20261227"
            ),
            service("ALL", "saturday sunday", "20260302", "20261227"),
        ],
    )
    with pytest.raises(FeedError, match="Saturday"):
        reference_week(read_service_calendar(path))


def test_a_feed_with_no_service_for_a_day_class_is_refused(tmp_path):
    path = write_feed(
        tmp_path,
        services=[
            service(
                "WD", "monday tuesday wednesday thursday friday", "20260302", "20261227"
            ),
            service("SU", "sunday", "20260302", "20261227"),
        ],
    )
    with pytest.raises(FeedError, match="sat"):
        reference_week(read_service_calendar(path))


def test_every_departure_in_the_table_gets_a_moment_inside_the_window(tmp_path):
    calendar = read_service_calendar(ordinary_feed(tmp_path))
    week = reference_week(calendar)

    moments = departures_for(week, calendar)

    assert set(moments) == {departure.matrix_key for departure in DEPARTURES}
    for departure in DEPARTURES:
        moment = moments[departure.matrix_key]
        assert moment.hour == departure.hour
        assert moment.tzinfo == calendar.timezone
        assert moment.date().weekday() == departure.day.weekday
        assert calendar.window[0] <= moment.date() <= calendar.window[1]


def test_the_same_feed_always_gives_the_same_departures(tmp_path):
    calendar = read_service_calendar(ordinary_feed(tmp_path))
    first = departures_for(reference_week(calendar), calendar)
    second = departures_for(reference_week(calendar), calendar)
    assert first == second
