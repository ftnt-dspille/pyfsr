"""Verify the inline examples in docstrings and docs actually reference real API.

These examples are *illustrative* — they assume a live ``client`` and a server,
so they can't be executed as plain doctests. But they can rot silently: a method
gets renamed, a path is wrong (``/api/v3/`` vs ``/api/3/``), an example calls a
method that never existed. This test catches that class of bug with no server:

  1. every example snippet is syntactically valid Python, and
  2. every ``client.<api>.<method>(...)`` / ``FortiSOAR(...)`` reference resolves
     to a real attribute/method on the actual classes.

Sources covered: ``>>>`` blocks in ``src/pyfsr`` docstrings + the ``python``
code-blocks in ``docs/source/index.rst``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from pyfsr import FortiSOAR
from pyfsr.records import RecordSet

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "pyfsr"
INDEX_RST = ROOT / "docs" / "source" / "index.rst"


# --- map the chain roots the examples use to the class that backs them ------- #
# client.<attr> sub-APIs are discovered from client.py so this never goes stale.
def _client_subapis() -> dict[str, type]:
    """Parse ``self.<name>: <Class> = <Class>(self)`` assignments in client.py."""
    tree = ast.parse((SRC / "client.py").read_text())
    import pyfsr.client as cl

    out: dict[str, type] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Attribute)
            and isinstance(node.target.value, ast.Name)
            and node.target.value.id == "self"
            and isinstance(node.annotation, ast.Name)
        ):
            cls = getattr(cl, node.annotation.id, None)
            if isinstance(cls, type):
                out[node.target.attr] = cls
    return out


SUBAPIS = _client_subapis()
# Chained calls whose return type we know, so deeper examples still resolve.
RETURNS = {"records": RecordSet}


def _iter_docstring_examples():
    """Yield (label, code) for each ``>>>`` block found in src docstrings."""
    for py in SRC.rglob("*.py"):
        try:
            mod = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(mod):
            doc = (
                ast.get_docstring(node, clean=True)
                if isinstance(
                    node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                )
                else None
            )
            if not doc or ">>>" not in doc:
                continue
            for i, block in enumerate(_extract_doctest_blocks(doc)):
                yield (f"{py.relative_to(ROOT)}::doc{i}", block)


def _extract_doctest_blocks(doc: str) -> list[str]:
    """Join ``>>>``/``...`` continuation lines into runnable snippets."""
    blocks, cur = [], []
    for line in doc.splitlines():
        s = line.strip()
        if s.startswith(">>> "):
            cur.append(s[4:])
        elif s.startswith("... "):
            cur.append(s[4:])
        elif s == ">>>" or s == "...":
            cur.append("")
        else:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def _iter_rst_examples():
    """Yield (label, code) for each ``.. code-block:: python`` in index.rst."""
    if not INDEX_RST.exists():
        return
    text = INDEX_RST.read_text()
    for i, m in enumerate(
        re.finditer(r"\.\. code-block:: python\n\n(.*?)(?:\n\S|\Z)", text, re.DOTALL)
    ):
        block = "\n".join(
            line[4:] if line.startswith("    ") else line for line in m.group(1).splitlines()
        )
        yield (f"docs/source/index.rst::block{i}", block.strip())


ALL_EXAMPLES = list(_iter_docstring_examples()) + list(_iter_rst_examples())


@pytest.mark.parametrize("label,code", ALL_EXAMPLES, ids=[e[0] for e in ALL_EXAMPLES])
def test_example_is_valid_python(label, code):
    """Every inline example must at least parse as Python."""
    ast.parse(code)


@pytest.mark.parametrize("label,code", ALL_EXAMPLES, ids=[e[0] for e in ALL_EXAMPLES])
def test_example_references_real_api(label, code):
    """Resolve ``client.<api>.<method>`` and ``FortiSOAR(...)`` against real classes."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = _attr_chain(node)
        if not chain:
            continue
        _assert_chain_resolves(chain, label)


def _attr_chain(node: ast.Attribute):
    """Flatten ``client.x.y`` into ['client'|'<call>', 'x', 'y']; None if not rooted."""
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
        # rooted at e.g. client.records("incidents").<...>
        inner = _attr_chain(cur.func)
        if inner and inner[0] == "client" and len(inner) == 2 and inner[1] in RETURNS:
            parts.append(f"@{inner[1]}")  # sentinel: resolved return type
            cur = ast.Name(id="client")
    if isinstance(cur, ast.Name) and cur.id == "client":
        parts.append("client")
        return list(reversed(parts))
    return None


def _assert_chain_resolves(chain: list[str], label: str):
    # chain[0] == 'client'
    if len(chain) < 2:
        return
    second = chain[1]
    if second.startswith("@"):  # client.records(...).<method>
        cls = RETURNS[second[1:]]
        if len(chain) >= 3:
            assert hasattr(cls, chain[2]), f"{label}: {cls.__name__}.{chain[2]} does not exist"
        return
    if second in SUBAPIS:
        if len(chain) >= 3:
            cls = SUBAPIS[second]
            assert hasattr(cls, chain[2]), (
                f"{label}: client.{second}.{chain[2]} — {cls.__name__} has no '{chain[2]}'"
            )
        return
    # otherwise it's a top-level client method (get/post/records/list_modules/...)
    assert hasattr(FortiSOAR, second), (
        f"{label}: client.{second} is not a FortiSOAR attribute/method"
    )
