"""A Source, through the scrape job, to the HTTP response.

One test covering the whole write path at once - the job, the normalisation, the
schema and the read - by registering a Source that yields a known Listing and
then asking the endpoint that serves what is on near a Rendezvous.

**Only the response is asserted.** Nothing here reaches into a repository or
counts rows: the point is what a caller can observe, and the layers in between
are expected to be rearranged by the work that follows.
"""

import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.contracts import Address, Located, Position
from app.core.enums import TravelMode
from app.jobs import scrape_events as job
from app.modules.events import sources as source_registry
from app.modules.events.scraped import RawListing, RawOccurrence
from app.modules.events.sources.spec import FetchContext, RawPayload, SourceSpec
from app.modules.meetups.models import Meetup, MeetupParticipant

VIENNA = ZoneInfo("Europe/Vienna")

# Stephansplatz. Somewhere the travel-time dataset certainly covers, so the
# Rendezvous for a single participant standing here is this cell.
LATITUDE, LONGITUDE = 48.2082, 16.3738


class StubSource:
    """A Source that yields one known Listing and never touches the network."""

    SPEC = SourceSpec(name="stub", locales=("de",), delay_seconds=0.0)

    def __init__(self, starts_at: datetime, title: str = "Stub concert") -> None:
        self.starts_at = starts_at
        self.title = title

    def fetch(self, ctx: FetchContext) -> Iterator[RawPayload]:
        yield RawPayload(url="stub://listing", body=b"{}")

    def parse(self, payload: RawPayload) -> Iterator[RawListing]:
        yield RawListing(
            source_event_id="stub-1",
            occurrences=[RawOccurrence(start=self.starts_at)],
            title=self.title,
            description="Something to do",
            url="https://example.invalid/stub-1",
            venue_name="Stub Hall",
            street="Stephansplatz 3",
            postcode="1010",
            city="Wien",
            lat=LATITUDE,
            lon=LONGITUDE,
            categories_raw=["Konzert"],
        )


class LentSession:
    """Hands the job the test's rolled-back session instead of a new one.

    The job opens its own session, having no request to hang one off. Here it
    borrows the fixture's, and must not close it - the assertions come after.
    """

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *_exc) -> bool:
        return False


@pytest.fixture
def meeting_at():
    """A meetup ten days out at 20:00 Vienna time.

    Relative to today rather than a fixed date, because normalisation refuses
    Occurrences outside its window and a hardcoded date would eventually fall
    outside it.
    """
    day = datetime.now(VIENNA).date() + timedelta(days=10)
    return datetime.combine(day, datetime.min.time(), tzinfo=VIENNA).replace(hour=20)


@pytest.fixture
def meetup(db, account, meeting_at) -> Meetup:
    """A meetup with one participant standing at the reference point."""
    row = Meetup(
        id=uuid.uuid4(),
        created_by_account_id=account.id,
        name="Coffee",
        location="Vienna",
        starts_at=meeting_at,
    )
    db.add(row)
    db.add(
        MeetupParticipant(
            id=uuid.uuid4(),
            meetup_id=row.id,
            created_by_account_id=account.id,
            name="Ada",
            travel_mode=TravelMode.WALK,
            latitude=LATITUDE,
            longitude=LONGITUDE,
        )
    )
    db.commit()
    return row


@pytest.fixture
def run_scrape(db, monkeypatch):
    """Register a Source and run the job over the test's own session.

    Geocoding is off unless a test hands over a `locate`: no test may depend on a
    geocoder being reachable, and none may ask a real one for an address.
    """

    def _run(source, locate=None) -> None:
        monkeypatch.setattr(
            source_registry, "discover", lambda: {source.SPEC.name: source}
        )
        monkeypatch.setattr(job, "SessionLocal", lambda: LentSession(db))
        monkeypatch.setattr(
            job.geocoding,
            "locator",
            lambda _db, enabled=True: locate or (lambda _: None),
        )
        job.scrape_events()

    return _run


async def _events(client, meetup) -> list[dict]:
    response = await client.get(f"/api/meetups/{meetup.id}/rendezvous/events")
    assert response.status_code == 200, response.text
    return response.json()


async def test_what_a_scrape_stored_is_served(client, meetup, meeting_at, run_scrape):
    run_scrape(StubSource(meeting_at + timedelta(minutes=30)))

    found = await _events(client, meetup)

    (event,) = [one for one in found if one["title"] == "Stub concert"]
    assert event["venue_name"] == "Stub Hall"
    assert event["address"] == "Stephansplatz 3, 1010 Wien"
    assert event["origin_url"] == "https://example.invalid/stub-1"
    # Wall-clock at the Venue, so the browser renders 20:30 rather than shifting it.
    assert event["starts_at"].startswith(f"{meeting_at.date().isoformat()}T20:30")
    assert event["all_day"] is False


async def test_a_second_run_does_not_duplicate_what_the_first_stored(
    client, meetup, meeting_at, run_scrape
):
    """Listings are upserted on the Source's own id, never appended."""
    run_scrape(StubSource(meeting_at + timedelta(minutes=30)))
    run_scrape(StubSource(meeting_at + timedelta(minutes=30)))

    found = await _events(client, meetup)

    assert [one["title"] for one in found].count("Stub concert") == 1


async def test_a_listing_that_stopped_appearing_is_no_longer_served(
    client, meetup, meeting_at, run_scrape
):
    run_scrape(StubSource(meeting_at + timedelta(minutes=30)))
    # The same Source, now listing something else entirely.
    replacement = StubSource(meeting_at + timedelta(minutes=45), title="Stub reading")
    replacement.parse = lambda payload: iter(
        [
            RawListing(
                source_event_id="stub-2",
                occurrences=[RawOccurrence(start=replacement.starts_at)],
                title="Stub reading",
                city="Wien",
                lat=LATITUDE,
                lon=LONGITUDE,
            )
        ]
    )
    run_scrape(replacement)

    titles = {one["title"] for one in await _events(client, meetup)}

    assert "Stub reading" in titles
    assert "Stub concert" not in titles


async def test_a_listing_whose_date_moved_leaves_the_old_day(
    client, meetup, meeting_at, run_scrape
):
    """A Source rescheduling a happening must not leave it on both days.

    The Listing survives - only its Occurrence moved - so nothing retires it,
    and the stale Occurrence went on being served on a day it was not on.
    """
    run_scrape(StubSource(meeting_at + timedelta(minutes=30)))
    assert "Stub concert" in {one["title"] for one in await _events(client, meetup)}

    # The same Listing, now two days later. The meetup is still on the old day.
    run_scrape(StubSource(meeting_at + timedelta(days=2, minutes=30)))

    assert "Stub concert" not in {one["title"] for one in await _events(client, meetup)}


async def test_a_listing_with_no_coordinates_is_placed_by_its_address(
    client, meetup, meeting_at, run_scrape
):
    """Most Sources publish no coordinates, and the read filters on distance - so
    without this the Listing exists and can never be shown to anyone."""
    unplaced = StubSource(meeting_at + timedelta(minutes=30), title="Needs placing")
    unplaced.parse = lambda payload: iter(
        [
            RawListing(
                source_event_id="stub-unplaced",
                occurrences=[RawOccurrence(start=unplaced.starts_at)],
                title="Needs placing",
                venue_name="Somewhere",
                street="Stephansplatz 3",
                postcode="1010",
                city="Wien",
            )
        ]
    )

    asked: list[Address] = []

    def locate(address: Address) -> Located | None:
        asked.append(address)
        return Located(
            position=Position(latitude=LATITUDE, longitude=LONGITUDE),
            precision="exact",
        )

    run_scrape(unplaced, locate=locate)

    assert "Needs placing" in {one["title"] for one in await _events(client, meetup)}
    # It was asked with what the Source actually gave, not with a guess.
    assert asked and asked[0].street == "Stephansplatz 3"


async def test_a_listing_the_source_already_placed_is_not_geocoded(
    client, meetup, meeting_at, run_scrape
):
    """The Source saw the happening; the geocoder is only reading words."""
    asked: list[Address] = []

    def locate(address: Address) -> Located | None:
        asked.append(address)
        return None

    run_scrape(StubSource(meeting_at + timedelta(minutes=30)), locate=locate)

    assert "Stub concert" in {one["title"] for one in await _events(client, meetup)}
    assert asked == []


async def test_a_source_that_cannot_be_fetched_fails_the_run(
    client, meetup, meeting_at, run_scrape
):
    """The runner turns this into a non-zero exit, which is what a scheduler reads."""
    broken = StubSource(meeting_at)
    broken.fetch = lambda ctx: (_ for _ in ()).throw(OSError("site is down"))

    with pytest.raises(RuntimeError, match="stub"):
        run_scrape(broken)


async def test_a_record_that_cannot_be_normalised_costs_only_itself(
    client, meetup, meeting_at, run_scrape
):
    """A record with no title goes to the quarantine rather than being dropped.

    Either way it must not reach a person - and the Listing alongside it must
    still arrive, which is what stops this passing on an empty catalogue.
    """
    mixed = StubSource(meeting_at + timedelta(minutes=30))
    good = next(iter(mixed.parse(None)))
    mixed.parse = lambda payload: iter(
        [
            good,
            RawListing(
                source_event_id="stub-untitled",
                occurrences=[RawOccurrence(start=mixed.starts_at)],
                title=None,
                city="Wien",
                lat=LATITUDE,
                lon=LONGITUDE,
            ),
        ]
    )
    run_scrape(mixed)

    titles = [one["title"] for one in await _events(client, meetup)]

    assert titles.count("Stub concert") == 1
    assert "Untitled event" not in titles
