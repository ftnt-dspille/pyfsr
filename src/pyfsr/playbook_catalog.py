"""Step-type catalog for playbook authoring -- the data behind ``pyfsr playbook
steps`` / ``step-help``.

This is the SDK/CLI twin of the ``get_step_type`` MCP discovery tool, but with
**no MCP dependency**: it reads the packaged ``fsr_playbooks`` reference catalog
(``fsr_reference.db``) and the compiler's friendly-type table directly, so it
works for anyone on the ``pyfsr[playbooks]`` extra.

Two surfaces:

* :func:`list_step_types` -- every friendly ``type:`` keyword an author can
  write, with its canonical FSR name and one-line purpose.
* :func:`step_help` -- one type in depth: label, purpose, pitfalls, the
  typed-arg JSON schema (when modeled), and a friendly-YAML example.

Examples are friendly YAML -- the same dialect you author in. Most are real
corpus snippets rendered through the decompiler; a few high-value types
(``manual_input``, ``workflow_reference``) carry a curated excerpt where the
corpus snippet would decompile to a verbose wire shape.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

# Friendly types with no distinct canonical step type (they compile down to a
# Connectors/no-op call), so the catalog label would be misleading. Give them a
# purpose of their own. Sourced from the resolver's own annotations.
_SYNTHETIC_PURPOSE: dict[str, str] = {
    "stop": "terminate a branch cleanly (compiles to a no-op connector step)",
    "end": "terminate a branch cleanly (compiles to a no-op connector step)",
    "delete_record": "delete a record (connector DELETE; FSR has no native delete step)",
    "ingest_bulk_feed": "bulk-insert feed records, bypassing on-create triggers",
    "insert_record": "create a record (legacy alias of create_record)",
}


# Curated friendly-YAML examples for types whose corpus snippet decompiles to a
# verbose wire shape (manual_input's dynamicList, etc.). These are lifted from
# examples/playbooks/do_until_validation_demo.yaml -- known-good, it compiles and
# round-trips. Preferred over the decompiled corpus snippet when present.
_CURATED_EXAMPLES: dict[str, str] = {
    "manual_input": """\
steps:
- name: AskNumber
  type: manual_input
  title: Enter a six digit number
  description: Please enter a number that is exactly 6 digits long.
  inputs:
  - {name: my_number, kind: integer, label: My Number, required: true}
  options:
  - {option: Submit, primary: true}
  next: Validate
# the submitted value is exposed at vars.steps.AskNumber.input.my_number
# drive it from the SDK with: client.manual_input.answer(654321, by_title="AskNumber")""",
    "workflow_reference": """\
steps:
- name: CallChild
  type: workflow_reference
  apply_async: false            # synchronous: parent waits for the child
  arguments:
    target: Validate Six Digit Number   # child playbook name (or UUID)
  retry:                        # do-until: re-run the child until the check passes
    until: '{{ vars.steps.CallChild.is_valid_number == true }}'
    times: 8
    delay: 1
  next: StampResult
# read the child's output as vars.steps.CallChild.<var the child set_variable'd>""",
}


def _load():
    """Lazy import of the compiler bits we need (clear error if extra missing)."""
    from fsr_playbooks.compiler.resolver import SHORT_TYPE_TO_FSR

    from .authoring import _load_compiler  # reuses the missing-extra message

    _, default_db_path = _load_compiler()
    return SHORT_TYPE_TO_FSR, default_db_path()


def _modeled_types() -> set[str]:
    """Friendly types with a typed-args JSON schema (best offline validation)."""
    try:
        from fsr_playbooks.compiler.typed_args.schema import list_modeled_step_types

        return set(list_modeled_step_types())
    except Exception:  # pragma: no cover - schema module is optional
        return set()


@dataclass
class StepTypeInfo:
    """One friendly step ``type:`` keyword and what it maps to."""

    short: str  # the friendly YAML keyword, e.g. "set_variable"
    canonical: str  # the FSR step type, e.g. "SetVariable"
    label: str  # the editor's palette label, e.g. "Set Variable"
    purpose: str  # one-line purpose
    modeled: bool  # has a typed-args schema (offline-validatable)


def list_step_types() -> list[StepTypeInfo]:
    """Every friendly ``type:`` keyword, sorted, with canonical name + purpose."""
    short_to_fsr, db = _load()
    modeled = _modeled_types()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = {r["name"]: r for r in conn.execute("SELECT name, label, description FROM step_types")}
    finally:
        conn.close()

    out: list[StepTypeInfo] = []
    for short, canonical in sorted(short_to_fsr.items()):
        row = rows.get(canonical)
        label = (row["label"] if row else None) or canonical
        purpose = _SYNTHETIC_PURPOSE.get(short) or _one_line(row["description"] if row else None) or label
        out.append(StepTypeInfo(short, canonical, label, purpose, short in modeled))
    return out


@dataclass
class StepHelp:
    """Authoring help for a single step type."""

    short: str
    canonical: str
    label: str
    purpose: str
    modeled: bool
    pitfalls: str | None = None
    arg_schema: dict[str, Any] | None = None
    example_yaml: str | None = None
    suggestions: list[str] = field(default_factory=list)


def step_help(name: str) -> StepHelp:
    """Author-facing help for one step type (friendly short name or canonical).

    Raises:
        KeyError: if ``name`` is not a known step type; the message lists close
            matches so the caller (or an agent) can correct it.
    """
    short_to_fsr, db = _load()
    canonical_to_short = {v: k for k, v in short_to_fsr.items()}

    if name in short_to_fsr:
        short, canonical = name, short_to_fsr[name]
    elif name in canonical_to_short:
        canonical, short = name, canonical_to_short[name]
    else:
        import difflib

        known = list(short_to_fsr) + list(canonical_to_short)
        near = difflib.get_close_matches(name, known, n=4, cutoff=0.4)
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        raise KeyError(f"unknown step type {name!r}.{hint}")

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT label, description, common_pitfalls FROM step_types WHERE name=?",
            (canonical,),
        ).fetchone()
        example = _CURATED_EXAMPLES.get(short) or _example_yaml(conn, canonical, db)
    finally:
        conn.close()

    label = (row["label"] if row else None) or canonical
    purpose = _SYNTHETIC_PURPOSE.get(short) or _one_line(row["description"] if row else None) or label
    return StepHelp(
        short=short,
        canonical=canonical,
        label=label,
        purpose=purpose,
        modeled=short in _modeled_types(),
        pitfalls=(row["common_pitfalls"] if row else None) or None,
        arg_schema=_arg_schema(short),
        example_yaml=example,
    )


def _arg_schema(short: str) -> dict[str, Any] | None:
    try:
        from fsr_playbooks.compiler.typed_args.schema import emit_step_arg_schema

        return emit_step_arg_schema(short)
    except Exception:  # pragma: no cover
        return None


def _example_yaml(conn: sqlite3.Connection, canonical: str, db: Any) -> str | None:
    """A real corpus example for ``canonical``, rendered as friendly YAML.

    Pulls one ``step_examples`` row (canonical wire ``{name, arguments}``), wraps
    it in a minimal collection envelope with the step type's real UUID, and runs
    it back through the decompiler so the author sees the friendly dialect they
    write in. Returns just the ``steps:`` block, or ``None`` if no example/render.
    """
    row = conn.execute(
        "SELECT snippet_json FROM step_examples WHERE step_type_name=? LIMIT 1",
        (canonical,),
    ).fetchone()
    uuid_row = conn.execute("SELECT uuid FROM step_types WHERE name=?", (canonical,)).fetchone()
    if not row or not uuid_row or not uuid_row["uuid"]:
        return None
    try:
        snippet = json.loads(row["snippet_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(snippet, dict):
        return None

    step = {
        **snippet,
        "stepType": f"/api/3/workflow_step_types/{uuid_row['uuid']}",
        "uuid": "00000000-0000-0000-0000-000000000001",
    }
    envelope = {
        "type": "workflow_collections",
        "data": [
            {
                "name": "example",
                "uuid": "example-collection",
                "workflows": [{"name": "Example", "uuid": "example-workflow", "steps": [step]}],
            }
        ],
    }
    try:
        from fsr_playbooks.compiler.decompiler import decompile_to_yaml

        full = decompile_to_yaml(envelope, db)
    except Exception:  # pragma: no cover - any decompile hiccup -> no example
        return None
    return _steps_block(full)


def _steps_block(full_yaml: str) -> str | None:
    """Extract the ``steps:`` block (dedented) from a decompiled collection."""
    lines = full_yaml.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "steps:":
            block = lines[i:]
            indent = len(block[0]) - len(block[0].lstrip())
            return "\n".join(ln[indent:] if len(ln) >= indent else ln for ln in block)
    return None


def _one_line(text: str | None) -> str | None:
    if not text:
        return None
    first = text.strip().splitlines()[0].strip()
    return first or None
