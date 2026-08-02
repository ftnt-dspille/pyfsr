#!/usr/bin/env python3
"""Find function parameters that are never read in the function body.

This is the scan that turned up `verify_llm_config(model_id=)` -- a parameter
whose docstring described behaviour ("sends it as part of the path") that the
code did not implement. That is the class of finding worth chasing: not the
unused parameter itself, which is often a deliberate interface decision, but the
*documentation that describes it as doing something*.

Reported findings are therefore split:

    DOCUMENTED   the parameter is unused AND named in the docstring. Either the
                 docstring is lying or the implementation is incomplete. Look at
                 every one of these.
    SILENT       unused and undocumented. Usually fine -- interface compat, a
                 signature matching a base class, a kwarg accepted and ignored
                 on purpose. Skim.

Ignored by default: `self`/`cls`, names starting with `_`, anything in a body
that is a bare `...`/`pass`/docstring (protocols, stubs, overloads), and
functions decorated with `@overload` or `@abstractmethod`.

    python3 scripts/find_unused_params.py                     # this repo's src/
    python3 scripts/find_unused_params.py ../other-repo/src
    python3 scripts/find_unused_params.py --all path1 path2   # include SILENT

Vendored trees swamp the signal -- a third-party corpus contributed 500+ of the
527 hits on its first run here -- so exclude them:

    python3 scripts/find_unused_params.py ../Miscellaneous/fortisoar \
        --exclude corpus_builder/repos --exclude cyops_utilities_source
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

SKIP_DECORATORS = {"overload", "abstractmethod", "abstractproperty"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "node_modules",
    "build",
    "dist",
    ".eggs",
}


def decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = set()
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def is_stub_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True for bodies that are only a docstring and/or `...`/`pass`.

    A stub cannot use its parameters by construction, so reporting one says
    nothing about the code.
    """
    for stmt in node.body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            # docstring, or a bare `...`
            if stmt.value.value is Ellipsis or isinstance(stmt.value.value, str):
                continue
        return False
    return True


def declared_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    a = node.args
    names = [arg.arg for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs)]
    # *args / **kwargs are excluded: they are routinely declared for
    # forwarding and pass-through, and flagging them is pure noise.
    return [n for n in names if n not in ("self", "cls") and not n.startswith("_")]


def names_read(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every identifier read anywhere in the body, including nested scopes.

    Nested functions and comprehensions are walked deliberately: a parameter
    captured by a closure is used, and treating it as unused would be wrong.
    """
    used: set[str] = set()
    for sub in node.body:
        for child in ast.walk(sub):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                used.add(child.id)
            # f-string / format-spec identifiers are ast.Name too, so they are
            # already covered. Bare `locals()`/`vars()` are not resolvable --
            # treat their presence as "uses everything" to avoid false hits.
            elif isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                if child.func.id in ("locals", "vars", "eval", "exec"):
                    used.add("*")
    return used


def scan_file(path: Path) -> list[tuple[int, str, str, bool]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        print(f"  ! skipped {path}: {exc}", file=sys.stderr)
        return []

    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if decorator_names(node) & SKIP_DECORATORS or is_stub_body(node):
            continue

        used = names_read(node)
        if "*" in used:
            continue
        doc = ast.get_docstring(node) or ""
        for param in declared_params(node):
            if param in used:
                continue
            findings.append((node.lineno, node.name, param, param in doc))
    return findings


def python_files(root: Path, excludes: list[str]):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*.py")):
        if SKIP_DIRS & set(path.parts):
            continue
        # Substring match on the posix path, so both a bare directory name
        # ("corpus_builder") and a partial path ("corpus_builder/repos") work.
        if any(pattern in path.as_posix() for pattern in excludes):
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=None, help="files or directories to scan (default: ./src)")
    parser.add_argument("--all", action="store_true", help="include undocumented (SILENT) findings")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="skip paths containing PATTERN (repeatable); use for vendored trees, which otherwise swamp the output",
    )
    args = parser.parse_args()

    roots = [Path(p) for p in (args.paths or ["src"])]
    documented, silent = [], []
    for root in roots:
        if not root.exists():
            print(f"! no such path: {root}", file=sys.stderr)
            continue
        for path in python_files(root, args.exclude):
            for lineno, func, param, in_doc in scan_file(path):
                (documented if in_doc else silent).append((path, lineno, func, param))

    for label, rows in (("DOCUMENTED", documented), ("SILENT", silent)):
        if label == "SILENT" and not args.all:
            continue
        print(f"\n== {label}  ({len(rows)})")
        for path, lineno, func, param in rows:
            print(f"  {path}:{lineno}  {func}({param}=)")

    if not args.all:
        print(f"\n({len(silent)} undocumented findings hidden; --all to show)")
    # Exit 1 only on DOCUMENTED: those are the ones where docs and code disagree.
    return 1 if documented else 0


if __name__ == "__main__":
    raise SystemExit(main())
