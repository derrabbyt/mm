"""A Source, through the scrape job, to the HTTP response.

One test covering the whole write path at once - the job, the normalisation, the
deduplication, the schema and the read - by registering Sources that yield known
Listings and then asking the endpoint that serves what is on near a Rendezvous.

**Only the response is asserted.** Nothing here reaches into a repository or
counts rows: the point is what a caller can observe, and the layers in between
are expected to be rearranged by the work that follows.

The two guards that make a run safe to schedule are the exception, and only as
far as they have to be. What an operator observes of a job is its log, so a guard
whose whole job is to refuse loudly is asserted on the log as well as on the
response - and one of them *arranges* through the repository, because taking the
run lock is the only way to put a run in progress without running one.

The payload archive is the other exception, for the same kind of reason: what it
keeps is never served, so no response can show whether a run archived anything,
and "every payload a run fetches is archived" is a promise about the run rather
than about the catalogue.
"""

import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.contracts import Address, Located, Position
from app.core.enums import TravelMode
from app.db.session import SessionLocal
from app.jobs import scrape_events as job
from app.modules.events import archive
from app.modules.events import repository as catalogue
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
            source_ref="stub-1",
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


class ListingSource:
    """A Source that yields exactly the Listings it is given.

    `StubSource` above is one happening described one way; this is what a
    second Source describing the same happening differently looks like.
    """

    def __init__(self, name: str, *listings: RawListing) -> None:
        self.SPEC = SourceSpec(name=name, locales=("de",), delay_seconds=0.0)
        self._listings = listings

    def fetch(self, ctx: FetchContext) -> Iterator[RawPayload]:
        yield RawPayload(url=f"stub://{self.SPEC.name}", body=b"{}")

    def parse(self, payload: RawPayload) -> Iterator[RawListing]:
        yield from self._listings


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

    def _run(*sources, locate=None) -> None:
        monkeypatch.setattr(
            source_registry,
            "discover",
            lambda: {source.SPEC.name: source for source in sources},
        )
        # The job connects as the scraping role; the test lends it the session
        # its own transaction is rolled back with.
        monkeypatch.setattr(job, "ScraperSessionLocal", lambda: LentSession(db))
        monkeypatch.setattr(
            job.geocoding,
            "locator",
            lambda _db, enabled=True: locate or (lambda _: None),
        )
        job.scrape_events()

    return _run


async def _events(client, meetup, **params) -> list[dict]:
    response = await client.get(
        f"/api/meetups/{meetup.id}/rendezvous/events", params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_run_archives_what_it_fetched(meetup, meeting_at, run_scrape):
    """Evidence for fixing a parser, which no response can show.

    Asserted through the archive's own reader rather than off the filesystem:
    what matters is that the payload comes back parseable, not where it landed.
    """
    run_scrape(StubSource(meeting_at + timedelta(minutes=30)))

    kept = list(archive.payloads_for("stub"))

    assert [one.url for one in kept] == ["stub://listing"]


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
                source_ref="stub-2",
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
                source_ref="stub-unplaced",
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
                source_ref="stub-untitled",
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


def listing(source_ref: str, title: str, starts_at: datetime, **kw) -> RawListing:
    """A Listing at the reference point unless a test says otherwise."""
    kw.setdefault("lat", LATITUDE)
    kw.setdefault("lon", LONGITUDE)
    return RawListing(
        source_ref=source_ref,
        occurrences=[RawOccurrence(start=starts_at)],
        title=title,
        **kw,
    )


async def test_one_happening_several_sources_listed_is_returned_once(
    client, meetup, meeting_at, run_scrape
):
    """The point of the whole absorption: 13.9% of what this endpoint returned
    was the same happening again, and one was returned as many as ten times.

    The entry is also the most complete one rather than whichever arrived
    first. The Listing that wins on overall completeness is the one with a
    position, and that is routinely not the one that names the Venue or
    carries a picture - the real "Afrika Tage" Event rendered as "@ ?" for
    exactly this reason.
    """
    starts_at = meeting_at + timedelta(minutes=30)
    run_scrape(
        ListingSource(
            "songkick",
            listing("sk-1", "Carpenter Brut", starts_at, street="Baumgasse 80"),
        ),
        ListingSource(
            "eventfinder",
            listing(
                "ef-1",
                "carpenter brut",
                starts_at,
                venue_name="Arena Wien",
                description="Synthwave, loud",
                image_url="https://example.invalid/brut.jpg",
                lat=None,
                lon=None,
            ),
        ),
        # Placed, like the first: without the grouping these two alone are
        # two entries for one gig.
        ListingSource(
            "wien_info",
            listing(
                "wi-1",
                "Carpenter Brut @ Arena Wien",
                starts_at,
                venue_name="Arena Wien",
            ),
        ),
    )

    found = [one for one in await _events(client, meetup) if "arpenter" in one["title"]]

    assert len(found) == 1
    (event,) = found
    assert event["venue_name"] == "Arena Wien"
    assert event["description"] == "Synthwave, loud"
    assert event["image_url"] == "https://example.invalid/brut.jpg"


async def test_two_happenings_at_one_venue_on_one_evening_stay_separate(
    client, meetup, meeting_at, run_scrape
):
    """Two productions in one house on one night are two things, whatever the
    Venue name says."""
    run_scrape(
        ListingSource(
            "events_at",
            listing(
                "ea-1",
                "Don Carlo",
                meeting_at + timedelta(minutes=30),
                venue_name="Wiener Staatsoper",
            ),
            listing(
                "ea-2",
                "La Bohème",
                meeting_at + timedelta(minutes=45),
                venue_name="Wiener Staatsoper",
            ),
        )
    )

    titles = {one["title"] for one in await _events(client, meetup)}

    assert {"Don Carlo", "La Bohème"} <= titles


async def test_venues_far_apart_stay_separate_even_with_similar_names(
    client, meetup, meeting_at, run_scrape
):
    """`wien` is a stop word, so "Orpheum Wien" is a token subset of "Orpheum
    Graz" and the names agree. Only the distance between them says otherwise.

    The second position is in Vienna rather than in Graz on purpose: that is
    where the geocoder put "Orpheum Graz", 6.8 km from the other Orpheum, and
    a pin in Styria would make the case easier than the real one. See ADR
    0002 - a show in another city must never be presented as around the
    corner.
    """
    starts_at = meeting_at + timedelta(minutes=30)
    run_scrape(
        ListingSource(
            "events_at",
            listing("ea-1", "DanzerMania", starts_at, venue_name="Orpheum Wien"),
        ),
        ListingSource(
            "eventfinder",
            listing(
                "ef-1",
                "DanzerMania",
                starts_at,
                venue_name="Orpheum Graz",
                lat=48.2434,
                lon=16.4481,
            ),
        ),
    )

    found = [
        one
        for one in await _events(client, meetup, radius_meters=10_000)
        if one["title"] == "DanzerMania"
    ]

    assert len(found) == 2
    assert {one["venue_name"] for one in found} == {"Orpheum Wien", "Orpheum Graz"}


async def test_a_listing_with_no_venue_name_is_still_served(
    client, meetup, meeting_at, run_scrape
):
    """3.6% of Listings name no Venue at all. A known position is enough to be
    worth showing, and it is why the distance veto needs both sides placed."""
    run_scrape(
        ListingSource(
            "wien_gv_at",
            listing("gv-1", "Nameless open air", meeting_at + timedelta(minutes=30)),
        )
    )

    (event,) = [
        one
        for one in await _events(client, meetup)
        if one["title"] == "Nameless open air"
    ]
    assert event["venue_name"] is None


async def test_the_day_is_the_meetups_local_day_not_the_utc_one(
    db, client, meetup, meeting_at, run_scrape
):
    """A gathering at 00:30 in Vienna is still the previous day in UTC, and the
    programme it should show is the local day's."""
    after_midnight = datetime.combine(
        meeting_at.date(), datetime.min.time(), tzinfo=VIENNA
    ).replace(hour=0, minute=30)
    assert after_midnight.astimezone(UTC).date() != after_midnight.date()
    meetup.starts_at = after_midnight
    db.commit()

    run_scrape(
        ListingSource(
            "goodnight",
            listing("gn-1", "Late set", after_midnight + timedelta(minutes=30)),
        )
    )

    assert "Late set" in {one["title"] for one in await _events(client, meetup)}


async def test_a_day_whose_last_listing_disappeared_is_served_no_longer(
    client, meetup, meeting_at, run_scrape
):
    """A day the run finds nothing on still has to be rebuilt.

    Retiring a Listing does not delete its Occurrences, so an Event nobody
    rebuilds goes on being served off the back of them. The day has to be
    emptied deliberately, which means a rebuild cannot only visit the days
    that still have something on them.
    """
    run_scrape(StubSource(meeting_at + timedelta(minutes=30)))
    assert "Stub concert" in {one["title"] for one in await _events(client, meetup)}

    # The same Source, now listing one thing days later and nothing at all on
    # the meetup's day.
    run_scrape(
        ListingSource(
            "stub", listing("stub-9", "Next week", meeting_at + timedelta(days=5))
        )
    )

    assert "Stub concert" not in {one["title"] for one in await _events(client, meetup)}


async def test_the_richest_listing_is_the_one_the_event_reads_as(
    client, meetup, meeting_at, run_scrape
):
    """Where two Sources both filled a field and disagree, the fuller Listing
    wins - the entry is the most complete one rather than whichever arrived
    first. Completeness decides it, so the Source named second alphabetically
    is not thereby the poorer answer.
    """
    starts_at = meeting_at + timedelta(minutes=30)
    run_scrape(
        ListingSource(
            "aaa_full",
            listing(
                "full-1",
                "Deep Purple",
                starts_at,
                venue_name="Wiener Stadthalle",
                street="Roland-Rainer-Platz 1",
                description="Doors at 19:00, support act announced",
                image_url="https://example.invalid/full.jpg",
                ticket_url="https://example.invalid/tickets",
                price_min=49.0,
                origin_url="https://stadthalle.test/deep-purple",
            ),
        ),
        ListingSource(
            "zzz_thin",
            listing(
                "thin-1",
                "DEEP PURPLE",
                starts_at,
                venue_name="Stadthalle Wien",
                description="Konzert",
                origin_url="https://aggregator.test/12345",
            ),
        ),
    )

    (event,) = [
        one for one in await _events(client, meetup) if one["title"] == "Deep Purple"
    ]
    assert event["description"] == "Doors at 19:00, support act announced"
    assert event["origin_url"] == "https://stadthalle.test/deep-purple"


# Five things on one evening, named so that no two of them can be read as the
# same happening: the matcher compares titles first, and these share no token.
PROGRAMME = ("Alpha", "Bravo", "Charlie", "Delta", "Echo")
OTHER_PROGRAMME = ("Foxtrot", "Golf", "Hotel", "India", "Juliett")


def programme(source: str, titles: Sequence[str], starts_at: datetime) -> ListingSource:
    """A Source listing each of `titles` at the reference point."""
    return ListingSource(
        source,
        *(
            listing(f"{source}-{index}", title, starts_at)
            for index, title in enumerate(titles)
        ),
    )


async def test_a_run_started_while_one_is_in_progress_changes_nothing(
    client, meetup, meeting_at, run_scrape, caplog
):
    """Two overlapping runs would each retire what the other had just written.

    What says a Listing is still real is the run that last saw it, so a second
    run stamping its own id retires the first run's Listings underneath it, and
    the first run then does the same back. The second one declines instead.
    """
    run_scrape(StubSource(meeting_at + timedelta(minutes=30)))

    # What a run in progress is holding, on its own connection. Not a second
    # scrape - only the one thing a second scrape would be holding, which is
    # what the guard actually reads.
    with SessionLocal() as other, catalogue.scrape_lock(other) as held:
        assert held, "nothing else may be holding the scrape lock"
        run_scrape(
            ListingSource(
                "stub",
                listing("stub-2", "Stub reading", meeting_at + timedelta(minutes=45)),
            )
        )

    titles = {one["title"] for one in await _events(client, meetup)}

    # Nothing written, and nothing retired either.
    assert "Stub reading" not in titles
    assert "Stub concert" in titles
    # Clean, and loud enough to tell an operator which of the two it was.
    assert "another run" in caplog.text


async def test_a_source_that_collapsed_retires_nothing(
    client, meetup, meeting_at, run_scrape, caplog
):
    """A site that changed its markup looks exactly like a site that cancelled
    almost everything. The share is the difference: a Source does not lose most
    of its programme between two runs, so the run refuses and says so."""
    starts_at = meeting_at + timedelta(minutes=30)
    run_scrape(programme("stub", PROGRAMME, starts_at))
    assert set(PROGRAMME) <= {one["title"] for one in await _events(client, meetup)}

    # The same Source, now returning one of the five it had.
    run_scrape(programme("stub", PROGRAMME[:1], starts_at))

    assert set(PROGRAMME) <= {one["title"] for one in await _events(client, meetup)}
    # Loudly, because a Source that really did shrink this far wants a person.
    assert "refusing to retire 4 of 5" in caplog.text
    assert any(record.levelname == "ERROR" for record in caplog.records)


async def test_one_source_collapsing_does_not_stop_another_retiring(
    client, meetup, meeting_at, run_scrape
):
    """The share is each Source's own, so one broken site costs only its own
    retirements - a run is not all-or-nothing across twenty-one of them."""
    starts_at = meeting_at + timedelta(minutes=30)
    run_scrape(
        programme("stub", PROGRAMME, starts_at),
        programme("other", OTHER_PROGRAMME, starts_at),
    )

    # One Source collapses to a single Listing; the other drops one, which is
    # the kind of change a Source really does make.
    run_scrape(
        programme("stub", PROGRAMME[:1], starts_at),
        programme("other", OTHER_PROGRAMME[:-1], starts_at),
    )

    titles = {one["title"] for one in await _events(client, meetup)}

    assert set(PROGRAMME) <= titles
    assert set(OTHER_PROGRAMME[:-1]) <= titles
    assert OTHER_PROGRAMME[-1] not in titles
