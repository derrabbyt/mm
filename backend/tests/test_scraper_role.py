"""The scraping job's own database role, and what it is not allowed to touch.

This is the reason ADR 0001 put scraped content in a schema of its own rather
than beside the application's tables, and the reason is only delivered once the
database itself refuses. So these tests connect as the scraper role for real and
ask Postgres, rather than asserting on the SQL a migration happens to contain: a
grant that looks right and a grant that works are different claims.

A bug in scraping - a source going haywire, a delete with the wrong predicate -
must not be able to reach an account or a meetup. Everything below is that
sentence, restated as things the database will not let this role do.
"""

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


# Skipped only where the migration that creates the role has not run - a
# database that has never heard of it has nothing for these to ask about. Once
# it exists they run, configured or not: a developer with an older `.env` would
# otherwise turn fifteen tests about what the scraper cannot reach into a green
# run that checked nothing.
pytestmark = pytest.mark.skipif(
    not _role_exists(),
    reason=(
        "the scraper role does not exist in this database, so the migration "
        "that grants it has not been run here"
    ),
)

# Tables the scraping job has no business writing to, one per shape of damage:
# an account is a person, a meetup is their plan, and a participant is the
# association between them.
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
    """A connection as the scraping job, rolled back whatever it managed to do."""
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
        """Everything else here is vacuous if it is.

        A superuser bypasses every grant, so the one way for this whole
        arrangement to be quietly worthless is for the role to be created with
        the wrong attributes.
        """
        assert (
            as_scraper.execute(
                text("select usesuper from pg_user where usename = current_user")
            ).scalar()
            is False
        )

    def test_it_can_write_the_catalogue_it_owns(self, as_scraper):
        """The job's actual work: a Listing, in the schema it is there to fill."""
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
        """Retiring Listings is part of a run, so it needs more than insert."""
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
        """One run at a time is enforced with an advisory lock, which the job
        cannot take if the role may not."""
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
        """Not even read. A scrape has no reason to know who the users are."""
        with pytest.raises(ProgrammingError, match="permission denied"):
            as_scraper.execute(text(f"select * from public.{table} limit 1"))

    @pytest.mark.parametrize("table", APPLICATION_TABLES)
    def test_it_cannot_write_an_application_table(self, as_scraper, table):
        with pytest.raises(ProgrammingError, match="permission denied"):
            as_scraper.execute(text(f"delete from public.{table}"))

    def test_it_cannot_create_a_table_in_the_application_schema(self, as_scraper):
        """A role that can create in `public` can shadow what is already there."""
        with pytest.raises(ProgrammingError, match="permission denied"):
            as_scraper.execute(text("create table public.scraper_was_here (id int)"))

    def test_it_cannot_reach_the_migration_history(self, as_scraper):
        """Schema changes are a migration's job, run as the owner - see 13."""
        with pytest.raises(ProgrammingError, match="permission denied"):
            as_scraper.execute(text("select * from public.alembic_version"))


class TestGrantsThatOutliveThisMigration:
    def test_a_content_table_added_later_is_covered(self, scraper_engine):
        """Default privileges, so the next table does not silently lose them.

        Without `alter default privileges`, adding a content table would leave
        the job unable to write it, and the failure would land in a scheduled
        run at three in the morning rather than here.

        The table is created and dropped for real, on a connection of its own:
        the scraper is a separate session, so a table made inside the rolled-back
        fixture transaction would be invisible to it.
        """
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
        """Checked against what is actually there, not against a list here.

        `alter default privileges` binds to whoever created the table, so a
        migration run as a different role leaves one the job cannot write - and
        that failure would otherwise surface in a scheduled run rather than
        here. This asks the database which content tables exist and whether the
        job can write each one, so adding a table wrong fails the suite.
        """
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
        """Every content table has an identity column, and an insert that
        cannot reach the sequence behind it fails at run time."""
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
