"""Check first-party imports for cycles that happen at import time.

Only imports executed when a module is imported count: module-level statements and class bodies,
including ``if``/``try``/``with`` blocks, except ``if TYPE_CHECKING:``. Imports inside functions are
the lazy imports this package uses on purpose to break cycles (``ordinary_form`` <-> ``table_field``,
``panel``, ``standards`` ...), so they are not edges. A relative import resolves to the longest
matching first-party module, so the result does not depend on set iteration order.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def _module_name(path: Path, source_root: Path) -> str:
    parts = list(path.relative_to(source_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")


def _import_time_nodes(tree: ast.AST):
    """Statements executed on import: skip function bodies and ``if TYPE_CHECKING:`` blocks."""
    stack = list(getattr(tree, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            stack.extend(node.orelse)
            continue
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                stack.append(child)


def import_graph(source_root: Path, package: str) -> dict[str, set[str]]:
    modules = {
        _module_name(path, source_root)
        for path in (source_root / package).rglob("*.py")
    }
    graph = {name: set() for name in modules}
    for path in (source_root / package).rglob("*.py"):
        current = _module_name(path, source_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _import_time_nodes(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = current.split(".")
                    # The current module name includes the filename. A level-1
                    # import starts from its containing package, one component
                    # above the module itself.
                    base = ".".join(parts[: len(parts) - node.level])
                    target = base + ("." + node.module if node.module else "")
                elif node.module:
                    target = node.module
                else:
                    continue
                matches = [candidate for candidate in modules
                           if target == candidate or target.startswith(candidate + ".")]
                if matches:
                    graph[current].add(max(matches, key=len))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules:
                        graph[current].add(alias.name)
    return graph


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    visiting: list[str] = []
    complete: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        if node in visiting:
            cycles.append(visiting[visiting.index(node):] + [node])
            return
        if node in complete:
            return
        visiting.append(node)
        for target in sorted(graph[node]):
            visit(target)
        visiting.pop()
        complete.add(node)

    for module in sorted(graph):
        visit(module)
    return cycles


def main() -> int:
    parser = argparse.ArgumentParser(description="Check first-party package imports for cycles.")
    parser.add_argument("--json", type=Path, help="write graph and cycle proof to this JSON file")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    graph = import_graph(repo / "src", "one_c_form_compiler")
    cycles = find_cycles(graph)
    result = {"modules": {name: sorted(targets) for name, targets in sorted(graph.items())}, "cycles": cycles}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if cycles:
        print(json.dumps(cycles, indent=2))
        return 1
    print(f"No import cycles across {len(graph)} first-party modules.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
