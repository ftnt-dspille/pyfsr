#!/usr/bin/env python3
"""Generate ``pyfsr/models/_generated.py`` from the curated OpenAPI spec.

The FortiSOAR OpenAPI spec (curated, in the sibling ``fortisoar-api-docs`` repo)
carries hand-tended schemas for the core entities. This script turns those
schemas into Pydantic v2 model classes subclassing :class:`pyfsr.models.BaseRecord`.

Usage::

    python scripts/gen_models.py [path/to/fortisoar.curated.openapi.yaml]

The default spec path matches the layout on the maintainer's machine; pass an
explicit path in CI or elsewhere. The generated file is committed — re-run this
whenever the spec's entity schemas change.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import yaml

# Entities to emit, in order. Keys must match component schema names.
ENTITIES = ["Alert", "Incident", "Task", "Comment"]

DEFAULT_SPEC = (
    Path.home()
    / "PycharmProjects/Miscellaneous/fortisoar-api-docs/build/fortisoar.curated.openapi.yaml"
)
OUT = Path(__file__).resolve().parent.parent / "src/pyfsr/models/_generated.py"

# Reserved/awkward field names → safe Python attribute names (kept aliased).
RENAMES = {"@id": "id_iri", "@type": "record_type"}
# Fields already declared on BaseRecord — skip so we don't redeclare them.
INHERITED = {"@id", "@type", "uuid"}


def _scalar_type(prop: dict) -> str:
    """Map an OpenAPI property to a Python type annotation string."""
    if "$ref" in prop:
        return "str"  # IRI / UUID refs are strings on the wire
    t = prop.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if t == "array":
        return "list[str] | None"  # arrays here are always IRI lists
    return {
        "string": "str | None",
        "integer": "int | None",
        "number": "float | None",
        "boolean": "bool | None",
    }.get(t, "Any | None")


def _field_line(name: str, prop: dict) -> str:
    py_name = RENAMES.get(name, name)
    ann = _scalar_type(prop)
    if py_name != name:
        return f'    {py_name}: {ann} = Field(default=None, alias="{name}")'
    return f"    {py_name}: {ann} = None"


def _docstring(schema: dict) -> str:
    desc = (schema.get("description") or "").strip().replace("\n", " ")
    while "  " in desc:
        desc = desc.replace("  ", " ")
    return desc


def build(spec: dict) -> str:
    schemas = spec["components"]["schemas"]

    # Build the class bodies first so we know which imports are actually needed.
    body: list[str] = []
    for entity in ENTITIES:
        schema = schemas[entity]
        body.append("")
        body.append(f"class {entity}(BaseRecord):")
        doc = _docstring(schema)
        if doc:
            lines = textwrap.wrap(doc, width=92)
            if len(lines) == 1:
                body.append(f'    """{lines[0]}"""')
            else:
                body.append(f'    """{lines[0]}')
                body.extend(f"    {line}" for line in lines[1:])
                body.append('    """')
            body.append("")
        wrote = False
        for name, prop in schema.get("properties", {}).items():
            if name in INHERITED:
                continue
            body.append(_field_line(name, prop))
            wrote = True
        if not wrote:
            body.append("    pass")
        body.append("")

    body_text = "\n".join(body)
    imports = ["from __future__ import annotations", ""]
    if "Any" in body_text:
        imports += ["from typing import Any", ""]
    if "Field(" in body_text:
        imports += ["from pydantic import Field", ""]
    imports += ["from .base import BaseRecord", ""]

    header = [
        '"""Typed FortiSOAR entity models — GENERATED, do not edit by hand.',
        "",
        "Regenerate with ``python scripts/gen_models.py``. Field sets come from the",
        "curated FortiSOAR OpenAPI spec; unknown fields are still preserved at runtime",
        "via ``BaseRecord``'s ``extra='allow'``.",
        '"""',
        "",
    ]
    return "\n".join(header + imports + body) + "\n"


def main() -> int:
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SPEC
    if not spec_path.exists():
        print(f"spec not found: {spec_path}", file=sys.stderr)
        return 1
    spec = yaml.safe_load(spec_path.read_text())
    OUT.write_text(build(spec))
    print(f"wrote {OUT} ({len(ENTITIES)} models from {spec_path.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
