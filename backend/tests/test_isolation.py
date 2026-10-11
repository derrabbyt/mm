from sqlalchemy import inspect, text

from app.core.config import settings
from app.db.base import CONTENT_SCHEMA
from app.db.session import engine

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
        assert _database_of(settings.database_url) != _database_of(
            settings.app_database_url
        )

    def test_the_connection_tests_use_is_the_test_database(self):
        with engine.connect() as connection:
            live = connection.execute(text("select current_database()")).scalar()

        assert live == _database_of(settings.database_url)
        assert live != _database_of(settings.app_database_url)

    def test_the_scraping_job_also_points_at_the_test_database(self):
        assert _database_of(settings.scraper_database_url) == _database_of(
            settings.database_url
        )


class TestItHoldsOnlyWhatATestPutThere:
    def test_the_schema_is_there(self):
        present = set(inspect(engine).get_table_names(schema=CONTENT_SCHEMA))

        assert set(CATALOGUE_TABLES) <= present

    def test_the_catalogue_starts_empty(self, db):
        counts = {
            table: db.execute(
                text(f"select count(*) from {CONTENT_SCHEMA}.{table}")
            ).scalar()
            for table in CATALOGUE_TABLES
        }

        assert counts == dict.fromkeys(CATALOGUE_TABLES, 0)

    def test_what_one_test_commits_is_gone_by_the_next(self, db):
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
        assert (
            db.execute(
                text(
                    f"select count(*) from {CONTENT_SCHEMA}.listings "
                    "where source = 'isolation-probe'"
                )
            ).scalar()
            == 0
        )
