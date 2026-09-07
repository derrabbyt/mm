"""Which Occurrences count as "on" a given day.

An Occurrence is a range, not a day. A museum open all year is one row dated at
its start with duration_days=364, so a query matching date_local alone hid it
for the other 364 days - 26% of what is on in a typical day.
"""

from datetime import UTC, date, datetime

import pytest

from app.core.contracts import Position
from app.modules.events import service
from app.modules.events.models import Listing, Occurrence

VIENNA = Position(latitude=48.2082, longitude=16.3738)
DAY = date(2026, 9, 15)
EVENING = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)


@pytest.fixture
def listing(db):
    """A geocoded Listing at the reference point, with one Occurrence."""

    def _make(title: str, starts: datetime, day: date, duration: int, all_day: bool):
        row = Listing(
            source="test",
            source_event_id=title,
            lang_primary="de",
            title_de=title,
            title_en="",
            lat=VIENNA.latitude,
            lon=VIENNA.longitude,
            first_seen_run="test-run",
        )
        db.add(row)
        db.flush()
        db.add(
            Occurrence(
                listing_id=row.id,
                start_utc=starts,
                # A wall-clock column: naive, or Postgres casts it through the
                # session timezone and shifts the value it exists to preserve.
                start_local=starts.replace(tzinfo=None),
                date_local=day,
                all_day=all_day,
                duration_days=duration,
            )
        )
        db.commit()
        return row.id

    return _make


def _titles(db):
    found = service.get_events_near(
        db, position=VIENNA, day=DAY, not_before=EVENING, radius_meters=500, limit=50
    )
    return {e.title for e in found}


def test_an_event_on_the_day_is_found(db, listing):
    listing("Concert tonight", datetime(2026, 9, 15, 20, tzinfo=UTC), DAY, 0, False)
    assert "Concert tonight" in _titles(db)


def test_an_event_earlier_that_day_is_not_found(db, listing):
    listing("Matinee", datetime(2026, 9, 15, 11, tzinfo=UTC), DAY, 0, False)
    assert "Matinee" not in _titles(db)


def test_an_all_day_event_is_found_whatever_the_hour(db, listing):
    listing("Street festival", datetime(2026, 9, 15, tzinfo=UTC), DAY, 0, True)
    assert "Street festival" in _titles(db)


def test_a_run_that_started_months_ago_is_still_open(db, listing):
    """The regression this file exists for: a museum open all year is one row
    dated 1 January, and used to vanish for the other 364 days."""
    listing(
        "Museum of Applied Arts",
        datetime(2026, 1, 1, tzinfo=UTC),
        date(2026, 1, 1),
        364,
        False,
    )
    assert "Museum of Applied Arts" in _titles(db)


def test_a_run_reports_itself_as_open_all_day_rather_than_starting_in_january(
    db, listing
):
    listing(
        "Long exhibition",
        datetime(2026, 1, 1, tzinfo=UTC),
        date(2026, 1, 1),
        364,
        False,
    )
    (found,) = [
        e
        for e in service.get_events_near(
            db,
            position=VIENNA,
            day=DAY,
            not_before=EVENING,
            radius_meters=500,
            limit=50,
        )
        if e.title == "Long exhibition"
    ]
    assert found.all_day is True
    assert found.starts_at.date() == DAY


def test_a_run_that_ended_before_the_day_is_not_found(db, listing):
    listing(
        "Closed in July", datetime(2026, 7, 1, tzinfo=UTC), date(2026, 7, 1), 10, False
    )
    assert "Closed in July" not in _titles(db)


def test_a_run_starting_after_the_day_is_not_found(db, listing):
    listing(
        "Opens in October",
        datetime(2026, 10, 1, tzinfo=UTC),
        date(2026, 10, 1),
        30,
        False,
    )
    assert "Opens in October" not in _titles(db)
