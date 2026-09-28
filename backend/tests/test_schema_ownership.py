"""The scraped catalogue has exactly one owner, and it is this repo.

Until now two projects wrote these tables: a standalone scraper that created and
migrated them, and this application that read them. That arrangement is over -
the scrape is a job here, the schema is this repo's migrations, and a change to a
Listing's shape is one change in one place.

What is left to hold is that it stays that way, which is two things. Nothing
outside a migration may change the schema, so no second writer can quietly
reappear. And the concepts that were deliberately left behind must not drift
back in with a later migration that "restores" something.
"""

import ast
import pathlib
from typing import ClassVar

import pytest
from sqlalchemy import inspect

# Imported for the side effect of registering every model on Base.metadata,
# the same reason migrations/env.py imports it - without it "what the models
# declare" is only what this file happened to import.
from app import metadata  # noqa: F401
from app.db.base import CONTENT_SCHEMA, Base
from app.db.session import engine

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"

# Everything that ships, which is everything but the two places DDL belongs:
# `migrations/`, whose whole job it is, and `tests/`, where a test may build a
# table to check a grant reaches it. Scanning only `app/` would leave a script
# dropped in beside it unguarded, and "no code anywhere" would be a claim about
# one directory.
SHIPPED_FILES = sorted(
    one
    for one in BACKEND.rglob("*.py")
    if "__pycache__" not in one.parts
    and not one.is_relative_to(BACKEND / "migrations")
    and not one.is_relative_to(BACKEND / "tests")
    and not one.is_relative_to(BACKEND / ".venv")
)

DDL_VERBS = (
    "create table",
    "drop table",
    "alter table",
    "create schema",
    "drop schema",
    "create index",
    "drop index",
)


def _ddl_literals_in(source: str) -> set[str]:
    """DDL found in string literals that are not docstrings.

    Literals rather than raw text, because a comment or a docstring saying that
    DDL belongs in a migration is not DDL. f-strings are walked too: building a
    statement out of a variable is exactly how one gets past a naive check.
    """
    tree = ast.parse(source)
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
    }

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value in docstrings:
            continue
        lowered = node.value.lower()
        found.update(verb for verb in DDL_VERBS if verb in lowered)
    return found


def _forbidden_calls_in(source: str, forbidden: set[str]) -> set[str]:
    """Method calls by name, so `Base.metadata.create_all(...)` is caught."""
    return {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } & forbidden


class TestNoDdlOutsideAMigration:
    """Schema changes are a migration's business, and only a migration's.

    The scraper used to create its own tables on connect, which is how two
    projects ended up able to change the same schema from two places. The
    database enforces half of this now - the scraping role is granted no CREATE
    - but the API's role still owns everything, so the rule is worth stating.

    Enforced over everything that ships. `migrations/` is exempt because DDL is
    what it is for, and `tests/` because a test may build a table to check that
    a grant reaches it - see `test_scraper_role.py`.
    """

    # `create_all` is the usual way this comes back: convenient in a test
    # fixture, and then one import away from running against a real database.
    FORBIDDEN_CALLS: ClassVar[set[str]] = {"create_all", "drop_all"}

    @pytest.mark.parametrize(
        "path", SHIPPED_FILES, ids=lambda p: str(p.relative_to(BACKEND))
    )
    def test_no_metadata_create_all(self, path: pathlib.Path):
        offending = _forbidden_calls_in(path.read_text(), self.FORBIDDEN_CALLS)
        assert not offending, (
            f"{path.name} calls {', '.join(sorted(offending))} - the schema is "
            "a migration's to change, so that there is one place it changes."
        )

    @pytest.mark.parametrize(
        "path", SHIPPED_FILES, ids=lambda p: str(p.relative_to(BACKEND))
    )
    def test_no_ddl_in_a_sql_string(self, path: pathlib.Path):
        offending = sorted(_ddl_literals_in(path.read_text()))
        assert not offending, (
            f"{path.name} contains the SQL {', '.join(offending)} - DDL belongs "
            "in migrations/, where it is reviewed and ordered."
        )

    def test_the_scan_reads_sql_rather_than_prose(self):
        """Docstrings and comments discuss DDL; only a string that could be
        executed is a finding. Scanning the file as text would make writing
        about `create table` in a comment fail the suite, and the first person
        to hit that would delete the check rather than the sentence."""
        assert not _ddl_literals_in('"""A note about CREATE TABLE."""\n')
        assert not _ddl_literals_in("# alter table happens in migrations\nx = 1\n")
        assert _ddl_literals_in('db.execute(text("CREATE TABLE x (id int)"))')
        assert _ddl_literals_in("q = f'drop table {name}'")

    @pytest.mark.parametrize(
        "path", SHIPPED_FILES, ids=lambda p: str(p.relative_to(BACKEND))
    )
    def test_alembic_operations_stay_in_migrations(self, path: pathlib.Path):
        """`op.create_table(...)` is DDL that no SQL string would reveal.

        Importing alembic's operations outside `migrations/` is the other way
        the schema gets a second author, and it reads as perfectly ordinary
        code.
        """
        imported = {
            name.split(".")[0]
            for node in ast.walk(ast.parse(path.read_text()))
            for name in (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
        }
        assert "alembic" not in imported, (
            f"{path.name} imports alembic - schema changes belong in migrations/"
        )

    def test_the_call_scan_would_notice(self):
        """So that a file with nothing to find does not look like a pass."""
        assert _forbidden_calls_in(
            "Base.metadata.create_all(engine)", self.FORBIDDEN_CALLS
        )
        assert not _forbidden_calls_in("engine.connect()", self.FORBIDDEN_CALLS)


class TestWhatWasLeftBehind:
    """Five of the scraper's ten tables were not carried across.

    Three are the ones that were dropped on purpose: `venue_links`, the venue
    verdict table behind a human review workflow; `alert_state`, the alerting
    transition state; and `eventloader_meta`, which held the scraper's own
    `schema_version` row - Alembic is the version marker now, and two markers
    disagreeing is how a schema ends up with two owners again. Per-Source run
    statistics were kept, which is what makes alerting possible later.

    The other two were renamed rather than abandoned, and are listed here under
    their old names for the same reason: `cards` became `content.events` and
    `dedup_members` became `content.event_listings`, so either reappearing means
    something restored the old shape alongside the new one.
    """

    DROPPED: ClassVar[tuple[str, ...]] = (
        "venue_links",
        "alert_state",
        "eventloader_meta",
        "cards",
        "dedup_members",
    )

    @pytest.mark.parametrize("table", DROPPED)
    def test_it_is_not_in_the_models(self, table):
        declared = {one.name for one in Base.metadata.tables.values()}
        assert table not in declared

    @pytest.mark.parametrize("table", DROPPED)
    @pytest.mark.parametrize("schema", ["public", CONTENT_SCHEMA])
    def test_it_is_not_in_the_database(self, table, schema):
        """Both schemas, because `public` is where the scraper actually built.

        Its `CREATE TABLE` statements were unqualified, so every one of these
        was a `public` table. Checking only `content` would let a genuine
        leftover - the thing this is looking for - pass.
        """
        present = set(inspect(engine).get_table_names(schema=schema))
        assert table not in present

    def test_the_catalogue_is_exactly_what_the_models_declare(self):
        """No table in `content` that no model knows about.

        A leftover from the other project would show up here, and so would a
        table some future migration added without a model - both are the
        catalogue having a second author again.
        """
        declared = {
            one.name
            for one in Base.metadata.tables.values()
            if one.schema == CONTENT_SCHEMA
        }
        present = set(inspect(engine).get_table_names(schema=CONTENT_SCHEMA))

        assert present == declared, (
            f"only in the database: {sorted(present - declared)}; "
            f"only in the models: {sorted(declared - present)}"
        )

    def test_per_source_statistics_were_kept(self):
        """Dropped the alerting, kept what alerting would need."""
        assert f"{CONTENT_SCHEMA}.source_runs" in Base.metadata.tables


class TestNothingBridgesTwoProjects:
    def test_no_setting_points_at_an_export(self):
        from app.core import config

        # Over the field names themselves: `dir()` of the mapping would list
        # the mapping's own methods and pass whatever the settings held.
        assert not [name for name in config.Settings.model_fields if "export" in name]
