"""Which occurrences count as "on" a given day.

An occurrence is a range, not a day. A museum open all year is one row dated at
its start with duration_days=364, so a query matching date_local alone hid it
for the other 364 days - 26% of events on a typical day.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text

from app.core.contracts import Position
from app.modules.events import service

VIENNA = Position(latitude=48.2082, longitude=16.3738)
DAY = date(2026, 9, 15)
EVENING = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)


@pytest.fixture
def listing(db):
    """A geocoded listing at the reference point, with no occurrences yet.

    Written with raw SQL because these tables belong to the scraper: the model
    maps the columns we read, not the ones it needs to insert.
    """

    def _make(title: str, starts: datetime, day: date, duration: int, all_day: bool):
        event_id = db.execute(
            text("""
                INSERT INTO events (lang_primary, title_de, title_en, source,
                                    source_event_id, lat, lon, first_seen_run)
                VALUES ('de', :t, '', 'test', :sid, :lat, :lon, 'test-run')
                RETURNING id
            """),
            {
                "t": title,
                "sid": title,
                "lat": VIENNA.latitude,
                "lon": VIENNA.longitude,
            },
        ).scalar_one()
        db.execute(
            text("""
                INSERT INTO occurrences (event_id, start_utc, start_local,
                                         date_local, all_day, duration_days)
                VALUES (:e, :s, :s, :d, :a, :dur)
            """),
            {"e": event_id, "s": starts, "d": day, "a": all_day, "dur": duration},
        )
        db.commit()
        return event_id

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
