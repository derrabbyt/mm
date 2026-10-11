import ast
import pathlib
from typing import ClassVar

import pytest
from sqlalchemy import inspect

from app import metadata  # noqa: F401
from app.db.base import CONTENT_SCHEMA, Base
from app.db.session import engine

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP = BACKEND / "app"

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
    return {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } & forbidden


class TestNoDdlOutsideAMigration:
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
        assert not _ddl_literals_in('"""A note about CREATE TABLE."""\n')
        assert not _ddl_literals_in("# alter table happens in migrations\nx = 1\n")
        assert _ddl_literals_in('db.execute(text("CREATE TABLE x (id int)"))')
        assert _ddl_literals_in("q = f'drop table {name}'")

    @pytest.mark.parametrize(
        "path", SHIPPED_FILES, ids=lambda p: str(p.relative_to(BACKEND))
    )
    def test_alembic_operations_stay_in_migrations(self, path: pathlib.Path):
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
        assert _forbidden_calls_in(
            "Base.metadata.create_all(engine)", self.FORBIDDEN_CALLS
        )
        assert not _forbidden_calls_in("engine.connect()", self.FORBIDDEN_CALLS)


class TestWhatWasLeftBehind:
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
        present = set(inspect(engine).get_table_names(schema=schema))
        assert table not in present

    def test_the_catalogue_is_exactly_what_the_models_declare(self):
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
        assert f"{CONTENT_SCHEMA}.source_runs" in Base.metadata.tables


class TestNothingBridgesTwoProjects:
    def test_no_setting_points_at_an_export(self):
        from app.core import config

        assert not [name for name in config.Settings.model_fields if "export" in name]
