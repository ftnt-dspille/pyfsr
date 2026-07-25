"""``pyfsr jinja`` — query the FSR Jinja reference store (offline, no appliance).

The reference DB (``fsr_reference.db``) ships with the ``fsr_playbooks``
package and contains 170+ FortiSOAR-custom Jinja filters, 15 globals, 39
tests — all introspected from a live appliance with full signatures,
parameter docs, and 1,690 real usage examples from 1,669 playbooks.

Subcommands:
  find NAME     show a filter/global/test by name (signature + doc + examples)
  search QUERY  full-text search across filter names, docs, and examples
  list          list all filters (or --kind globals / --kind tests)
  examples NAME show real-world usage examples from the playbook corpus
  idioms        show common Jinja patterns from the idioms reference

Usage::

    pyfsr jinja find picklist
    pyfsr jinja search "query body"
    pyfsr jinja list --kind globals
    pyfsr jinja examples picklist
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path


def _find_db() -> Path | None:
    """Locate ``fsr_reference.db`` — prefer the full repo DB (65MB, has usage examples)
    over the slim bundled copy (20KB)."""
    # 1. The framework repo (dev) — full DB with 1,690 usage examples
    repo_paths = [
        Path(os.environ.get("FSR_PLAYBOOK_FRAMEWORK", "")),
        Path("/Users/dylanspille/PycharmProjects/fsr-playbook-framework"),
        Path.home() / "PycharmProjects" / "fsr-playbook-framework",
    ]
    for repo in repo_paths:
        if not repo.exists():
            continue
        db = repo / "data" / "fsr_reference.db"
        if db.exists() and db.stat().st_size > 1_000_000:
            return db

    # 2. fsr_playbooks package bundled data (slim — no usage examples, but has filters/globals/tests)
    try:
        import fsr_playbooks

        pkg_data = Path(fsr_playbooks.__file__).parent / "_data" / "fsr_reference.db"
        if pkg_data.exists():
            return pkg_data
    except ImportError:
        pass

    return None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _print_filter(row: sqlite3.Row, *, examples: list[sqlite3.Row] | None = None) -> None:
    """Pretty-print a Jinja filter/global/test from a DB row."""
    name = row["name"]
    sig = row["signature"] or ""
    desc = row["description"] or ""
    example = row["example"] or ""
    module = row["module"] or ""
    curated = row["curated_doc"] or ""
    out_type = row["output_type_observed"] or ""

    print(f"\n  {name}({sig})")
    if module:
        print(f"  module: {module}")
    if out_type:
        print(f"  output type: {out_type}")
    if desc:
        print(f"  {desc}")
    if curated:
        print(f"\n  {curated}")
    if example:
        print(f"\n  Example: {example}")
    if examples:
        print(f"\n  Real-world usage ({len(examples)} examples):")
        for ex in examples[:5]:
            expr = ex["expression"]
            pb = ex["from_playbook"] or ""
            step = ex["from_step"] or ""
            print(f"    {expr}")
            if pb:
                print(f"      from: {pb} > {step}")


def cmd_find(args: argparse.Namespace) -> int:
    db = _find_db()
    if db is None:
        print("ERROR: fsr_reference.db not found. Install fsr_playbooks or set FSR_PLAYBOOK_FRAMEWORK.")
        return 1

    conn = _connect(db)
    name = args.name.strip()

    # Check macros (filters) first, then globals, then tests
    for table in ("jinja_macros", "jinja_globals", "jinja_tests"):
        row = conn.execute(f"SELECT * FROM {table} WHERE name = ?", (name,)).fetchone()
        if row:
            kind = "filter" if table == "jinja_macros" else "global" if table == "jinja_globals" else "test"
            print(f"[{kind}]")
            examples: list[sqlite3.Row] = []
            if table == "jinja_macros":
                examples = conn.execute(
                    "SELECT * FROM jinja_filter_usage WHERE filter_name = ? ORDER BY occurrences DESC LIMIT 5",
                    (name,),
                ).fetchall()
            _print_filter(row, examples=examples)
            conn.close()
            return 0

    print(f"Filter/global/test '{name}' not found.")
    conn.close()
    return 1


def cmd_search(args: argparse.Namespace) -> int:
    db = _find_db()
    if db is None:
        print("ERROR: fsr_reference.db not found.")
        return 1

    conn = _connect(db)
    q = f"%{args.query.strip()}%"
    found = False

    for table, kind in (("jinja_macros", "filter"), ("jinja_globals", "global"), ("jinja_tests", "test")):
        try:
            sql = (
                f"SELECT * FROM {table} WHERE name LIKE ? OR description LIKE ? OR curated_doc LIKE ? OR example LIKE ?"
            )
            rows = conn.execute(sql, (q, q, q, q)).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            found = True
            print(f"[{kind}] {row['name']}({row['signature'] or ''})")
            if row["curated_doc"]:
                print(f"  {row['curated_doc'][:200]}")
            if row["example"]:
                print(f"  Example: {row['example']}")

    # Also search usage examples (may be absent in the slim bundled DB)
    try:
        sql = (
            "SELECT * FROM jinja_filter_usage WHERE expression LIKE ? OR from_playbook LIKE ? "
            "ORDER BY occurrences DESC LIMIT 10"
        )
        usage_rows = conn.execute(sql, (q, q)).fetchall()
    except sqlite3.OperationalError:
        usage_rows = []
    if usage_rows:
        found = True
        print(f"\n[usage] {len(usage_rows)} matching examples:")
        for row in usage_rows:
            print(f"  {row['expression']}")
            if row["from_playbook"]:
                print(f"    from: {row['from_playbook']} > {row['from_step']}")

    if not found:
        print(f"No matches for '{args.query}'.")
    conn.close()
    return 0 if found else 1


def cmd_list(args: argparse.Namespace) -> int:
    db = _find_db()
    if db is None:
        print("ERROR: fsr_reference.db not found.")
        return 1

    conn = _connect(db)
    kind = args.kind or "filters"

    table = {"filters": "jinja_macros", "globals": "jinja_globals", "tests": "jinja_tests"}.get(kind)
    if not table:
        print(f"Unknown kind '{kind}'. Use: filters, globals, tests")
        return 1

    rows = conn.execute(f"SELECT name, signature, module FROM {table} ORDER BY name").fetchall()
    print(f"{len(rows)} {kind}:")
    for row in rows:
        mod = f" ({row['module']})" if row["module"] else ""
        print(f"  {row['name']}({row['signature'] or ''}){mod}")
    conn.close()
    return 0


def cmd_examples(args: argparse.Namespace) -> int:
    db = _find_db()
    if db is None:
        print("ERROR: fsr_reference.db not found.")
        return 1

    conn = _connect(db)
    name = args.name.strip()
    rows = conn.execute(
        "SELECT * FROM jinja_filter_usage WHERE filter_name = ? ORDER BY occurrences DESC LIMIT 20",
        (name,),
    ).fetchall()

    if not rows:
        print(f"No usage examples found for '{name}'.")
        conn.close()
        return 1

    print(f"{len(rows)} usage examples for '{name}':")
    for row in rows:
        print(f"\n  {row['expression']}")
        if row["from_playbook"]:
            print(f"    from: {row['from_playbook']} > {row['from_step']} (step type: {row['step_type'] or '?'})")
        print(f"    occurrences: {row['occurrences']}")

    conn.close()
    return 0


def cmd_idioms(args: argparse.Namespace) -> int:
    """Print key Jinja idioms from the idioms reference doc."""
    idioms_path = None
    try:
        import fsr_playbooks

        p = Path(fsr_playbooks.__file__).parent / "_data" / "JINJA_IDIOMS.md"
        if p.exists():
            idioms_path = p
    except ImportError:
        pass

    if not idioms_path:
        repo = Path(
            os.environ.get("FSR_PLAYBOOK_FRAMEWORK", "/Users/dylanspille/PycharmProjects/fsr-playbook-framework")
        )
        p = repo / "data" / "JINJA_IDIOMS.md"
        if p.exists():
            idioms_path = p

    if not idioms_path:
        print("ERROR: JINJA_IDIOMS.md not found.")
        return 1

    text = idioms_path.read_text()
    # Print up to the first section or the whole thing if --full
    if args.full:
        print(text)
    else:
        # Print just the summary table + first 2 patterns
        lines = text.splitlines()
        printed = 0
        in_section = False
        for _i, line in enumerate(lines):
            if line.startswith("## "):
                if printed >= 3:
                    break
                in_section = True
                printed += 1
            if in_section:
                print(line)
    return 0


def build_subparser(sub: argparse._SubParsersAction) -> None:
    """Register ``pyfsr jinja`` subcommands (find, search, list, examples, idioms)."""
    p_find = sub.add_parser("find", help="show a filter/global/test by name")
    p_find.add_argument("name", help="filter name (e.g. 'picklist', 'currentDateMinus')")
    p_find.set_defaults(func=cmd_find)

    p_search = sub.add_parser("search", help="full-text search across names, docs, and examples")
    p_search.add_argument("query", help="search term (e.g. 'query body', 'picklist', 'date')")
    p_search.set_defaults(func=cmd_search)

    p_list = sub.add_parser("list", help="list all filters (or --kind globals/tests)")
    p_list.add_argument("--kind", default="filters", choices=["filters", "globals", "tests"])
    p_list.set_defaults(func=cmd_list)

    p_examples = sub.add_parser("examples", help="real-world usage examples from the playbook corpus")
    p_examples.add_argument("name", help="filter name (e.g. 'picklist')")
    p_examples.set_defaults(func=cmd_examples)

    p_idioms = sub.add_parser("idioms", help="common Jinja patterns from the idioms reference")
    p_idioms.add_argument("--full", action="store_true", help="print the entire idioms doc")
    p_idioms.set_defaults(func=cmd_idioms)
