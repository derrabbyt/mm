import ast
import pathlib

import pytest

MODULES_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "modules"

SOURCE_FILES = sorted(MODULES_DIR.glob("*/**/*.py"))


def _owner(path: pathlib.Path) -> str:
    return path.relative_to(MODULES_DIR).parts[0]


def _imported_targets(tree: ast.AST, package: list[str]) -> list[str]:
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            prefix = [*base, *(node.module.split(".") if node.module else [])]
            targets.append(".".join(prefix))
            targets.extend(".".join([*prefix, alias.name]) for alias in node.names)
    return targets


DB_FACING = {"service.py", "repository.py", "models.py", "jobs.py"}


def _crossings(path: pathlib.Path) -> list[str]:
    owner = _owner(path)
    package = ["app", "modules", *path.relative_to(MODULES_DIR).parts[:-1]]
    targets = _imported_targets(ast.parse(path.read_text()), package)
    return [
        t
        for t in targets
        if t.split(".")[:2] == ["app", "modules"]
        and len(t.split(".")) >= 3
        and t.split(".")[2] != owner
    ]


@pytest.mark.parametrize(
    "path", SOURCE_FILES, ids=lambda p: str(p.relative_to(MODULES_DIR))
)
def test_db_facing_layers_do_not_know_other_modules_exist(path: pathlib.Path):
    if path.name not in DB_FACING:
        pytest.skip(f"{path.name} is not a database-facing layer")

    reached = sorted({t for t in _crossings(path) if len(t.split(".")) >= 4})
    assert not reached, (
        f"{path.relative_to(MODULES_DIR.parent.parent)} holds a Session and imports "
        + ", ".join(reached)
        + ". Move the composition into router.py and pass values instead - see "
        "docs/architecture.md."
    )


@pytest.mark.parametrize(
    "path", SOURCE_FILES, ids=lambda p: str(p.relative_to(MODULES_DIR))
)
def test_module_only_reaches_another_module_through_its_public(path: pathlib.Path):
    owner = _owner(path)
    package = ["app", "modules", *path.relative_to(MODULES_DIR).parts[:-1]]
    tree = ast.parse(path.read_text())

    violations = []
    for target in _imported_targets(tree, package):
        parts = target.split(".")
        if parts[:2] != ["app", "modules"] or len(parts) < 3:
            continue
        other = parts[2]
        if other == owner:
            continue
        if len(parts) >= 4 and parts[3] != "public":
            violations.append(target)

    assert not violations, (
        f"{path.relative_to(MODULES_DIR.parent.parent)} reaches past "
        "another module's public.py: "
        + ", ".join(sorted(set(violations)))
        + ". Export what you need from that module's public.py and import it from there."
    )
