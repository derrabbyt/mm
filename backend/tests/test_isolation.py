"""The suite runs against a database of its own, holding only what a test put there.

Two separate promises, and the second is the one that kept being broken.

A test must not run against the database the application uses. Rolling back what
a test wrote is not enough on its own: the rollback protects the data, not the
test, and a `DELETE` or a `TRUNCATE` typed into the wrong fixture is one keystroke
from the real catalogue.

And a test must see only the rows it created. `test_events.py` stores one Listing
and asks what is on near a point; `test_scrape_events.py` registers a stub Source
and asks the endpoint what it serves. Neither takes the existing catalogue as an
input, so both silently depended on it being nearly empty - and a real scrape of
7,607 Listings put a "MAK - Museum for Applied Arts" 246 m from the reference
point and three Occurrences on the day one of them rebuilds. Deduplication did
exactly what ADR 0002 asks and absorbed the test's own row.

That the catalogue is empty here is also what keeps the suite quick: every scrape
test rebuilds the Events of every day the catalogue covers, so a populated one
cost sixteen minutes instead of ninety seconds.
"""

from sqlalchemy import inspect, text

from app.core.config import settings
from app.db.base import CONTENT_SCHEMA
from app.db.session import engine

# Every table a scrape writes. Each one is a way for a previous run - or a real
# one - to reach a test that never asked about it.
CATALOGUE_TABLES = (
    "listings",
    "occurrences",
    "events",
    "event_listings",
    "quarantine",
    "source_runs",
)


def _database_of(url: str) -> str:
    return url.rsplit("/", 1)[-1].partition("?")[0]


class TestItIsNotTheApplicationDatabase:
    def test_the_suite_has_a_database_of_its_own(self):
        """Named apart from the application's, so the two cannot be confused."""
        assert _database_of(settings.database_url) != _database_of(
            settings.app_database_url
        )

    def test_the_connection_tests_use_is_the_test_database(self):
        with engine.connect() as connection:
            live = connection.execute(text("select current_database()")).scalar()

        assert live == _database_of(settings.database_url)
        assert live != _database_of(settings.app_database_url)

    def test_the_scraping_job_also_points_at_the_test_database(self):
        """Otherwise a test that runs the job writes to the real catalogue
        through the other connection, whatever the first one is pointed at."""
        assert _database_of(settings.scraper_database_url) == _database_of(
            settings.database_url
        )


class TestItHoldsOnlyWhatATestPutThere:
    def test_the_schema_is_there(self):
        """An empty database and a database without the tables look the same to
        a count, and only one of them means the migrations ran."""
        present = set(inspect(engine).get_table_names(schema=CONTENT_SCHEMA))

        assert set(CATALOGUE_TABLES) <= present

    def test_the_catalogue_starts_empty(self, db):
        """The promise `test_events.py` and `test_scrape_events.py` both rely on
        without ever saying so."""
        counts = {
            table: db.execute(
                text(f"select count(*) from {CONTENT_SCHEMA}.{table}")
            ).scalar()
            for table in CATALOGUE_TABLES
        }

        assert counts == dict.fromkeys(CATALOGUE_TABLES, 0)

    def test_what_one_test_commits_is_gone_by_the_next(self, db):
        """The rolled-back transaction, asserted rather than assumed - it is
        what stops the tests above polluting each other."""
        db.execute(
            text(f"""
            insert into {CONTENT_SCHEMA}.listings
                (source, source_ref, title_norm, first_seen_run, last_seen_run)
            values ('isolation-probe', 'probe-1', 'probe', 'r', 'r')
            """)
        )
        db.commit()

        assert (
            db.execute(
                text(
                    f"select count(*) from {CONTENT_SCHEMA}.listings "
                    "where source = 'isolation-probe'"
                )
            ).scalar()
            == 1
        )

    def test_the_previous_test_left_nothing_behind(self, db):
        """Reads what the test above committed. Ordering is deliberate: this is
        the only way to observe the rollback from inside the suite."""
        assert (
            db.execute(
                text(
                    f"select count(*) from {CONTENT_SCHEMA}.listings "
                    "where source = 'isolation-probe'"
                )
            ).scalar()
            == 0
        )
