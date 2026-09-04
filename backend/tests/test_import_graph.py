"""The import graph has no cycles, per file and per layer.

Python only raises on a cycle when a module is read before it finishes
initialising, so a tangled graph can sit there for a long time and then break
on an unrelated reordering.
"""

import ast
import pathlib
import sys
from collections import defaultdict

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
FILES = sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def _name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(APP.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


KNOWN = {_name(p) for p in FILES}


def _imports(path: pathlib.Path) -> set[str]:
    """First-party modules this file imports, as absolute dotted paths."""
    package = _name(path).split(".")
    if path.name != "__init__.py":
        package = package[:-1]

    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            prefix = [*base, *(node.module.split(".") if node.module else [])]
            found.add(".".join(prefix))
            found.update(".".join([*prefix, a.name]) for a in node.names)
    return {m for m in found if m in KNOWN}


GRAPH = {_name(p): _imports(p) for p in FILES}


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's strongly connected components, keeping only the tangled ones."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    counter = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)

        for neighbour in graph.get(node, ()):
            if neighbour not in index:
                visit(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif neighbour in on_stack:
                low[node] = min(low[node], index[neighbour])

        if low[node] == index[node]:
            component = []
            while True:
                popped = stack.pop()
                on_stack.discard(popped)
                component.append(popped)
                if popped == node:
                    break
            if len(component) > 1 or node in graph.get(node, ()):
                found.append(sorted(component))

    sys.setrecursionlimit(10_000)
    for node in list(graph):
        if node not in index:
            visit(node)
    return found


def test_no_file_imports_itself_in_a_loop():
    cycles = _cycles(GRAPH)
    assert not cycles, "circular imports between files: " + "; ".join(
        " -> ".join(c) for c in cycles
    )


def _layer(module: str) -> str:
    """The architectural layer a module belongs to."""
    parts = module.split(".")
    if len(parts) >= 3 and parts[1] == "modules":
        return f"modules/{parts[2]}"
    return parts[1] if len(parts) > 1 else parts[0]


def test_no_layer_depends_on_a_layer_that_depends_on_it():
    collapsed: dict[str, set[str]] = defaultdict(set)
    for source, targets in GRAPH.items():
        for target in targets:
            here, there = _layer(source), _layer(target)
            if here != there:
                collapsed[here].add(there)

    cycles = _cycles(dict(collapsed))
    assert not cycles, "circular dependencies between layers: " + "; ".join(
        " <-> ".join(c) for c in cycles
    )


@pytest.mark.parametrize("path", [p for p in FILES if p.parent.name in {"db", "core"}])
def test_shared_plumbing_never_reaches_up_into_a_feature(path: pathlib.Path):
    """`core/` and `db/` sit below every module. Nothing in them may import one."""
    reached = sorted(m for m in _imports(path) if m.startswith("app.modules."))
    assert not reached, (
        f"{path.relative_to(APP.parent)} imports {', '.join(reached)}. "
        "Shared plumbing cannot depend on the features built on it - "
        "put the file where it is allowed to know, as app/metadata.py does."
    )
