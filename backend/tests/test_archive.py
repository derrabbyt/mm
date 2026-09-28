"""Keeping what a site actually served, so a broken parser has evidence.

The committed fixtures are the tripwire; this is what there is to look at when
the tripwire fires and nobody kept a copy of the document that set it off. So
the contract worth testing is narrow and specific: a payload goes in, and the
same payload - body, url, kind and the meta its parse depends on - comes back
out later with no network anywhere near it.
"""

import datetime as dt
import gzip
import json
import pathlib

import pytest

from app.modules.events import archive, replay, service
from app.modules.events.scraped import RawListing, RawOccurrence
from app.modules.events.sources import meinbezirk, wien_gv_at
from app.modules.events.sources.spec import (
    FetchContext,
    RawPayload,
    SourceSpec,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

FETCHED_AT = dt.datetime(2026, 9, 27, 14, 30, tzinfo=dt.UTC)


@pytest.fixture
def archive_dir() -> pathlib.Path:
    """Where `conftest.archive_under_tmp` already pointed the archive."""
    return archive.settings.scrape_archive_dir


def payload(**overrides) -> RawPayload:
    return RawPayload(
        **{
            "url": "https://example.invalid/events?page=1",
            "body": b"<html>a listing</html>",
            "kind": "listing",
            "fetched_at": FETCHED_AT,
            "meta": {"page": 1},
            **overrides,
        }
    )


class TestStoring:
    def test_a_payload_is_filed_under_its_source_and_its_day(self, archive_dir):
        archive.store(payload(), source="events_at")

        day = archive_dir / "events_at" / "2026-09-27"
        assert day.is_dir()
        assert list(day.glob("*.gz")), "the body is archived"

    def test_the_body_is_stored_compressed(self, archive_dir):
        """A run of wien_gv_at alone is ~110 MB of HTML, and it compresses ~10:1.

        Uncompressed, a fortnight's retention would be gigabytes.
        """
        stored = archive.store(payload(body=b"x" * 10_000), source="events_at")

        assert stored is not None
        assert stored.stat().st_size < 1_000
        assert gzip.decompress(stored.read_bytes()) == b"x" * 10_000

    def test_the_day_is_the_day_the_run_fetched_it_in_vienna(self, archive_dir):
        """Vienna's calendar, like every other window a scrape reckons in.

        22:30 UTC in September is already tomorrow in Vienna. Filing in UTC
        would expire the archive on a boundary nothing else in the scrape
        observes.
        """
        before_midnight = archive.store(
            payload(fetched_at=dt.datetime(2026, 9, 27, 21, 30, tzinfo=dt.UTC)),
            source="events_at",
        )
        after_midnight = archive.store(
            payload(
                url="https://example.invalid/events?page=2",
                fetched_at=dt.datetime(2026, 9, 27, 22, 30, tzinfo=dt.UTC),
            ),
            source="events_at",
        )

        assert before_midnight.parent.name == "2026-09-27"
        assert after_midnight.parent.name == "2026-09-28"

    def test_archiving_the_same_payload_twice_keeps_one_copy(self, archive_dir):
        """A run re-fetching a URL is not two documents worth keeping."""
        archive.store(payload(), source="events_at")
        archive.store(payload(), source="events_at")

        assert len(list((archive_dir / "events_at" / "2026-09-27").glob("*.gz"))) == 1

    def test_a_later_run_does_not_overwrite_what_an_earlier_one_saw(self, archive_dir):
        """The scrape runs hourly, and this is the whole point of the archive.

        Named after where it came from, each run would overwrite the last and
        the 14:00 document that broke a parser would be gone by 15:00 - exactly
        the document somebody goes looking for.
        """
        archive.store(
            payload(
                body=b"<html>the good one</html>",
                fetched_at=dt.datetime(2026, 9, 27, 14, 0, tzinfo=dt.UTC),
            ),
            source="events_at",
        )
        archive.store(
            payload(
                body=b"<html>the one that broke the parser</html>",
                fetched_at=dt.datetime(2026, 9, 27, 15, 0, tzinfo=dt.UTC),
            ),
            source="events_at",
        )

        bodies = {one.body for one in archive.payloads_for("events_at")}
        assert bodies == {
            b"<html>the good one</html>",
            b"<html>the one that broke the parser</html>",
        }

    def test_a_page_that_did_not_change_costs_one_copy(self, archive_dir):
        """Content-addressed, so twenty-four identical hourly fetches are one
        document. That is what keeps the archive affordable."""
        for hour in range(6):
            archive.store(
                payload(fetched_at=dt.datetime(2026, 9, 27, hour, tzinfo=dt.UTC)),
                source="events_at",
            )

        assert len(list(archive.payloads_for("events_at"))) == 1

    def test_one_url_fetched_with_different_meta_is_two_documents(self, archive_dir):
        """meinbezirk asks one path per day, and the day is only in the meta.

        Keyed on the URL alone, each day's page would overwrite the last and the
        archive would hold one day of a sixty-day window.
        """
        archive.store(payload(meta={"date": "2026-08-15"}), source="meinbezirk")
        archive.store(payload(meta={"date": "2026-08-16"}), source="meinbezirk")

        stored = {one.meta["date"] for one in archive.payloads_for("meinbezirk")}
        assert stored == {"2026-08-15", "2026-08-16"}

    def test_a_source_whose_name_would_escape_the_archive_writes_nothing(
        self, archive_dir
    ):
        """A Source's name reaches the filesystem, and it is free text.

        `SPEC.name` is whatever a Source says it is, so an unusable one is a
        mistake in that Source - it must cost the evidence, not the run. So:
        refused, nothing written, and no exception for the scrape to trip over.
        """
        assert archive.store(payload(), source="../../etc") is None
        assert not archive_dir.exists()

    def test_a_failed_write_does_not_stop_the_run(self, archive_dir, monkeypatch):
        """The archive is evidence, not the product.

        A full disk must cost the evidence and nothing else - it must not fail a
        Source that fetched and parsed perfectly well.
        """

        def explode(*args, **kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr(archive.gzip, "compress", explode)

        assert archive.store(payload(), source="events_at") is None


class TestReadingBack:
    def test_what_went_in_comes_back_out(self, archive_dir):
        archive.store(payload(), source="events_at")

        (restored,) = archive.payloads_for("events_at")

        original = payload()
        assert restored.url == original.url
        assert restored.body == original.body
        assert restored.kind == original.kind
        assert restored.meta == original.meta
        assert restored.fetched_at == FETCHED_AT

    def test_a_day_can_be_asked_for_on_its_own(self, archive_dir):
        archive.store(payload(), source="events_at")
        archive.store(
            payload(fetched_at=dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.UTC)),
            source="events_at",
        )

        assert len(list(archive.payloads_for("events_at"))) == 2
        assert len(list(archive.payloads_for("events_at", dt.date(2026, 9, 27)))) == 1

    def test_an_unarchived_source_is_empty_rather_than_an_error(self):
        assert list(archive.payloads_for("never_scraped")) == []

    def test_a_body_with_no_sidecar_is_skipped(self, archive_dir):
        """Half a write is not a payload: without the sidecar there is no kind
        and no meta, and a parse handed the wrong kind yields silence."""
        stored = archive.store(payload(), source="events_at")
        stored.with_suffix(".json").unlink()

        assert list(archive.payloads_for("events_at")) == []

    def test_a_meinbezirk_parse_replays_off_the_archive(self, archive_dir):
        """The whole point, on a Source whose parse needs its meta.

        meinbezirk reads the queried day out of the payload's meta - there is no
        year in the cards - so a replay that lost the meta would yield nothing
        while looking like a parser that had broken.
        """
        original = RawPayload(
            url="https://www.meinbezirk.at/event/wien/list",
            body=(FIXTURES / "meinbezirk" / "listing-2026-08-15.html").read_bytes(),
            kind="listing",
            meta={"date": "2026-08-15", "page": 1},
        )
        archive.store(original, source="meinbezirk")

        (replayed,) = archive.payloads_for("meinbezirk")

        assert [one.source_ref for one in meinbezirk.parse(replayed)] == [
            one.source_ref for one in meinbezirk.parse(original)
        ]

    def test_a_wien_gv_at_parse_replays_off_the_archive(self, archive_dir):
        """The same, for a Source whose meta carries its coordinates."""
        meta = json.loads((FIXTURES / "wien_gv_at" / "detail-meta.json").read_text())
        original = RawPayload(
            url="https://www.wien.gv.at/veranstaltungen/20-jahre-palart",
            body=(FIXTURES / "wien_gv_at" / "detail-subevent.html").read_bytes(),
            kind="detail",
            meta=meta,
        )
        archive.store(original, source="wien_gv_at")

        (replayed,) = archive.payloads_for("wien_gv_at")
        (listing,) = wien_gv_at.parse(replayed)

        assert listing.lat == pytest.approx(meta["lat"])
        assert listing.lon == pytest.approx(meta["lon"])
        assert len(listing.occurrences) > 1


class TestExpiring:
    def _archive_on(self, day: dt.date, source: str = "events_at") -> None:
        archive.store(
            payload(
                url=f"https://example.invalid/{day}",
                fetched_at=dt.datetime.combine(day, dt.time(12), tzinfo=dt.UTC),
            ),
            source=source,
        )

    def test_days_past_the_retention_go(self, archive_dir):
        for day in (dt.date(2026, 9, 1), dt.date(2026, 9, 20), dt.date(2026, 9, 27)):
            self._archive_on(day)

        removed = archive.prune(retention_days=14, today=dt.date(2026, 9, 27))

        assert removed == 1
        kept = sorted(one.name for one in (archive_dir / "events_at").iterdir())
        assert kept == ["2026-09-20", "2026-09-27"]

    def test_the_retention_counts_days_available_including_today(self, archive_dir):
        """Retention of 14 leaves fourteen days to look at, today being one.

        From the 27th that reaches back to the 14th; the 13th is the fifteenth
        day and goes. Worth pinning, because off by one here is the difference
        between a fortnight of evidence and a fortnight minus the day someone
        needs.
        """
        self._archive_on(dt.date(2026, 9, 14))
        self._archive_on(dt.date(2026, 9, 13))

        removed = archive.prune(retention_days=14, today=dt.date(2026, 9, 27))

        assert removed == 1
        kept = [one.name for one in (archive_dir / "events_at").iterdir()]
        assert kept == ["2026-09-14"]

    def test_every_source_is_pruned_not_only_the_first(self, archive_dir):
        self._archive_on(dt.date(2026, 9, 1), source="events_at")
        self._archive_on(dt.date(2026, 9, 1), source="goabase")

        assert archive.prune(retention_days=14, today=dt.date(2026, 9, 27)) == 2

    def test_something_that_is_not_a_day_is_left_alone(self, archive_dir):
        """Pruning deletes directories, so it only touches what it recognises."""
        stray = archive_dir / "events_at" / "notes"
        stray.mkdir(parents=True)

        assert archive.prune(retention_days=1, today=dt.date(2026, 9, 27)) == 0
        assert stray.is_dir()

    def test_pruning_an_archive_that_does_not_exist_yet_is_not_an_error(self):
        assert archive.prune(retention_days=14, today=dt.date(2026, 9, 27)) == 0

    def test_a_kind_that_is_not_fit_for_a_filename_is_flattened(self, archive_dir):
        """A payload's kind is free text a Source chooses, and it names a file.

        Unlike a Source's name it only labels the file, so an awkward one is
        worth flattening rather than refusing the evidence over.
        """
        stored = archive.store(payload(kind="Detail Page/v2"), source="events_at")

        assert stored is not None
        assert stored.name.startswith("detail-page-v2-")
        assert next(archive.payloads_for("events_at")).kind == "Detail Page/v2"


class _TwoPageSource:
    """A Source that yields two payloads and never touches the network."""

    SPEC = SourceSpec(name="stub_archived", delay_seconds=0.0)

    def __init__(self, *, parse_raises: bool = False) -> None:
        self.parse_raises = parse_raises

    def fetch(self, ctx: FetchContext):
        for page in (1, 2):
            yield RawPayload(
                url=f"https://example.invalid/list?page={page}",
                body=f"<html>page {page}</html>".encode(),
                kind="listing",
                fetched_at=FETCHED_AT,
                meta={"page": page},
            )

    def parse(self, payload: RawPayload):
        if self.parse_raises:
            raise ValueError("this document broke the parser")
        yield RawListing(
            source_ref=f"stub-{payload.meta['page']}",
            occurrences=[RawOccurrence(start=dt.date(2026, 9, 27))],
            title="Stub",
        )


class TestWhatAScrapeArchives:
    """`_collect` is the seam: it is where a run turns payloads into Listings,
    and it needs no database to exercise."""

    @staticmethod
    def _context() -> FetchContext:
        return FetchContext(
            http=None,  # type: ignore[arg-type]
            date_from=dt.date(2026, 9, 27),
            date_to=dt.date(2026, 9, 28),
        )

    def test_every_payload_a_run_fetches_is_archived(self, archive_dir):
        listings, error = service._collect(_TwoPageSource(), self._context())

        assert error is None
        assert len(listings) == 2
        assert len(list(archive.payloads_for("stub_archived"))) == 2

    def test_a_payload_that_breaks_the_parser_is_archived_anyway(self, archive_dir):
        """The one document most worth keeping is the one that just failed.

        Archiving after parsing would lose exactly the payload someone needs.
        """
        listings, error = service._collect(
            _TwoPageSource(parse_raises=True), self._context()
        )

        assert listings == []
        assert error is None, "a document that will not parse is not a failed fetch"
        assert len(list(archive.payloads_for("stub_archived"))) == 2

    def test_what_was_archived_parses_back_to_the_same_listings(self, archive_dir):
        """Compared as a set: a payload is named by what it is, so the archive
        gives them back in a stable order that is not the order they arrived."""
        source = _TwoPageSource()
        live, _ = service._collect(source, self._context())

        replayed = [
            one
            for payload in archive.payloads_for("stub_archived")
            for one in source.parse(payload)
        ]

        assert {one.source_ref for one in replayed} == {one.source_ref for one in live}
        assert len(replayed) == len(live)


class TestReplaying:
    """The archive made reachable: `python -m app.modules.events.replay <source>`."""

    def _archive_a_meinbezirk_day(self) -> None:
        archive.store(
            RawPayload(
                url="https://www.meinbezirk.at/event/wien/list",
                body=(FIXTURES / "meinbezirk" / "listing-2026-08-15.html").read_bytes(),
                kind="listing",
                fetched_at=FETCHED_AT,
                meta={"date": "2026-08-15", "page": 1},
            ),
            source="meinbezirk",
        )

    def test_a_source_is_reparsed_off_its_archive(self, archive_dir):
        self._archive_a_meinbezirk_day()

        found = replay.replay("meinbezirk")

        assert len(found) == 8

    def test_one_day_can_be_replayed_on_its_own(self, archive_dir):
        self._archive_a_meinbezirk_day()

        assert replay.replay("meinbezirk", dt.date(2026, 9, 27))
        assert replay.replay("meinbezirk", dt.date(2026, 9, 26)) == []

    def test_a_source_nobody_has_heard_of_says_so(self):
        with pytest.raises(SystemExit) as refused:
            replay.replay("not_a_source")

        assert "not_a_source" in str(refused.value)

    def test_one_broken_document_does_not_end_the_replay(
        self, archive_dir, monkeypatch
    ):
        """A replay exists to find every payload that breaks a parser, not the
        first one - that is the difference between one loop and twenty."""
        self._archive_a_meinbezirk_day()
        archive.store(
            RawPayload(
                url="https://www.meinbezirk.at/event/wien/list?page=2",
                body=b"<html>not what the parser expects</html>",
                kind="listing",
                fetched_at=FETCHED_AT,
                meta={"date": "2026-08-15", "page": 2},
            ),
            source="meinbezirk",
        )

        def explode(payload):
            if b"not what" in payload.body:
                raise ValueError("boom")
            return iter(())

        monkeypatch.setattr(
            replay.sources.discover()["meinbezirk"], "parse", explode, raising=False
        )

        assert replay.replay("meinbezirk") == []

    def test_it_reaches_no_database_and_no_network(self):
        """It is a debugging tool over saved documents, and must stay one."""
        text = pathlib.Path(replay.__file__).read_text()
        for forbidden in ("sqlalchemy", "Session", "HttpClient", "requests"):
            assert forbidden not in text
