"""Output rendering for the pyfsr CLI: table / JSON / CSV, with secret redaction."""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Sequence
from typing import Any, TextIO


def render(
    rows: Sequence[Sequence[Any]],
    headers: Sequence[str] | None = None,
    *,
    fmt: str = "table",
    file: TextIO = sys.stdout,
) -> None:
    """Render tabular ``rows`` in the requested format (``table``/``json``/``csv``)."""
    rows = [[("" if c is None else str(c)) for c in row] for row in rows]
    if fmt == "json":
        payload: list[Any] = [dict(zip(headers, row, strict=False)) for row in rows] if headers else list(rows)
        json.dump(payload, file, indent=2, default=str)
        file.write("\n")
        return
    if fmt == "csv":
        writer = csv.writer(file)
        if headers:
            writer.writerow(headers)
        writer.writerows(rows)
        return
    _render_table(rows, headers, file)


def _render_table(rows: Sequence[Sequence[Any]], headers: Sequence[str] | None, file: TextIO) -> None:
    cols = list(headers) if headers else []
    width = [len(h) for h in cols]
    for row in rows:
        for i, cell in enumerate(row):
            if i >= len(width):
                width.append(len(cell))
                if i >= len(cols):
                    cols.append("")
            else:
                width[i] = max(width[i], len(cell))
    if headers:
        file.write("  ".join(h.ljust(width[i]) for i, h in enumerate(cols)) + "\n")
        file.write("  ".join("-" * width[i] for i in range(len(cols))) + "\n")
    for row in rows:
        file.write("  ".join(str(cell).ljust(width[i]) for i, cell in enumerate(row)) + "\n")


def kv(pairs: dict[str, Any], *, fmt: str = "table", file: TextIO = sys.stdout) -> None:
    """Render a key/value identity card."""
    if fmt == "json":
        json.dump(pairs, file, indent=2, default=str)
        file.write("\n")
        return
    width = max((len(k) for k in pairs), default=0)
    for k, v in pairs.items():
        file.write(f"{k.ljust(width)}  {v}\n")


def parse_psql_columns(stdout: str) -> tuple[list[str], list[list[str]]]:
    """Split a psql ``-A -F\\x1f`` *with-header* result into (headers, rows)."""
    lines = [ln for ln in stdout.splitlines() if ln.strip() != ""]
    if not lines:
        return [], []
    headers = lines[0].split("\x1f")
    rows = [ln.split("\x1f") for ln in lines[1:]]
    return headers, rows
