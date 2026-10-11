import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from app.core.config import settings
from app.db.base import CONTENT_SCHEMA
from app.db.session import engine


def _role_exists() -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                sa.text("select 1 from pg_roles where rolname = :role"),
                {"role": settings.scraper_postgres_user},
            ).scalar()
        )


pytestmark = pytest.mark.skipif(
    not _role_exists(),
    reason=(
        "the scraper role does not exist in this database, so the migration "
        "that grants it has not been run here"
    ),
)

APPLICATION_TABLES = ("accounts", "meetups", "meetup_participants")


@pytest.fixture(scope="module")
def scraper_engine():
    engine = create_engine(settings.scraper_database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def as_scraper(scraper_engine):
    connection = scraper_engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


class TestWhatItMay:
    def test_it_connects_as_itself_and_not_as_the_application(self, as_scraper):
        assert as_scraper.execute(text("select current_user")).scalar() == (
            settings.scraper_postgres_user
        )

    def test_it_is_not_a_superuser(self, as_scraper):
        assert (
            as_scraper.execute(
                text("select usesuper from pg_user where usename = current_user")
            ).scalar()
            is False
        )

    def test_it_can_write_the_catalogue_it_owns(self, as_scraper):
        as_scraper.execute(
            text(f"""
                insert into {CONTENT_SCHEMA}.listings
                    (source, source_ref, title_norm, first_seen_run, last_seen_run)
                values ('stub', 'role-test-1', 'role test', 'r1', 'r1')
            """)
        )

        assert (
            as_scraper.execute(
                text(
                    f"select count(*) from {CONTENT_SCHEMA}.listings "
                    "where source_ref = 'role-test-1'"
                )
            ).scalar()
            == 1
        )

    def test_it_can_read_and_delete_within_the_catalogue(self, as_scraper):
        for statement in (
            f"select count(*) from {CONTENT_SCHEMA}.listings",
            f"delete from {CONTENT_SCHEMA}.listings where source_ref = 'absent'",
            (
                f"update {CONTENT_SCHEMA}.listings set title_norm = title_norm "
                "where source_ref = 'absent'"
            ),
        ):
            as_scraper.execute(text(statement))

    def test_it_can_take_the_run_lock(self, as_scraper):
        assert (
            as_scraper.execute(
                text("select pg_try_advisory_lock(hashtext('scrape-role-test'))")
            ).scalar()
            is True
        )
        as_scraper.execute(
            text("select pg_advisory_unlock(hashtext('scrape-role-test'))")
        )


class TestWhatItMayNot:
    @pytest.mark.parametrize("table", APPLICATION_TABLES)
    def test_it_cannot_read_an_application_table(self, as_scraper, table):
        with pytest.raises(ProgrammingError, match="permission denied"):
            as_scraper.execute(text(f"select * from public.{table} limit 1"))

    @pytest.mark.parametrize("table", APPLICATION_TABLES)
    def test_it_cannot_write_an_application_table(self, as_scraper, table):
        with pytest.raises(ProgrammingError, match="permission denied"):
            as_scraper.execute(text(f"delete from public.{table}"))

    def test_it_cannot_create_a_table_in_the_application_schema(self, as_scraper):
        with pytest.raises(ProgrammingError, match="permission denied"):
            as_scraper.execute(text("create table public.scraper_was_here (id int)"))

    def test_it_cannot_reach_the_migration_history(self, as_scraper):
        with pytest.raises(ProgrammingError, match="permission denied"):
            as_scraper.execute(text("select * from public.alembic_version"))


class TestGrantsThatOutliveThisMigration:
    def test_a_content_table_added_later_is_covered(self, scraper_engine):
        later = f"{CONTENT_SCHEMA}.added_after_the_grants"
        owner = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            owner.execute(text(f"create table {later} (id int primary key)"))
            # In a block of its own, and closed before the drop: the insert
            # holds a lock, and a `drop table` waiting on it is a hung suite
            # rather than a failing test.
            with scraper_engine.connect() as scraper:
                scraper.execute(text(f"insert into {later} (id) values (1)"))
                scraper.rollback()
        finally:
            owner.execute(text(f"drop table if exists {later}"))
            owner.close()

    def test_every_table_in_the_catalogue_is_within_reach(self, as_scraper):
        unreachable = (
            as_scraper.execute(
                text(f"""
                select c.relname
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = '{CONTENT_SCHEMA}' and c.relkind = 'r'
                  and not has_table_privilege(
                      current_user, c.oid, 'SELECT,INSERT,UPDATE,DELETE'
                  )
            """)
            )
            .scalars()
            .all()
        )

        assert not unreachable, (
            f"the scraping job cannot write {', '.join(unreachable)} - a content "
            "table was created without the grants reaching it"
        )

    def test_it_can_use_a_sequence_in_the_catalogue(self, as_scraper):
        granted = as_scraper.execute(
            text(f"""
                select bool_and(
                    has_sequence_privilege(current_user, c.oid, 'USAGE,SELECT')
                )
                from pg_class c
                join pg_namespace n on n.oid = c.relnamespace
                where c.relkind = 'S' and n.nspname = '{CONTENT_SCHEMA}'
            """)
        ).scalar()

        assert granted is not False, "a content sequence is out of reach"
