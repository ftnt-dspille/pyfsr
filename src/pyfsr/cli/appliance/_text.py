"""Shared parsing helpers for csadm's free-form text output.

csadm emits human-formatted text (ANSI-coloured status lines, ``key : value``
cards, dash-ruled tables) rather than JSON. These helpers turn that into the
structured values the typed ``service`` / ``ha`` / ``license`` wrappers return.
"""

from __future__ import annotations

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Drop ANSI SGR colour codes (csadm colours ``[Running]`` etc.)."""
    return _ANSI.sub("", text)


def kv_pairs(text: str, *, sep: str = ":") -> dict[str, str]:
    """Parse a ``Key   : Value`` card into a dict (keys/values stripped).

    Splits each line on the first ``sep``; lines without it (preamble, rules) are
    skipped. csadm pads keys with spaces and uses ``" : "`` — leading/trailing
    whitespace is removed so ``"Total Users    : 2"`` → ``{"Total Users": "2"}``.
    """
    out: dict[str, str] = {}
    for line in strip_ansi(text).splitlines():
        key, found, val = line.partition(sep)
        if not found:
            continue
        key = key.strip()
        if key:
            out[key] = val.strip()
    return out


def dash_columns(sep_line: str) -> list[tuple[int, int]]:
    """Column ``(start, end)`` spans from a dash-rule line (``---  ----  --``).

    Lets fixed-width tables with space-containing cells (e.g. an HA ``comment`` of
    ``"primary server"``) be sliced by position instead of split on whitespace.
    """
    return [(m.start(), m.end()) for m in re.finditer(r"-+", sep_line)]


def slice_columns(line: str, spans: list[tuple[int, int]]) -> list[str]:
    """Slice ``line`` by ``spans`` (last span runs to end-of-line); cells stripped."""
    cells: list[str] = []
    for i, (start, end) in enumerate(spans):
        cells.append(line[start : (None if i == len(spans) - 1 else end)].strip())
    return cells


def to_int(s: str | None, default: int | None = None) -> int | None:
    """Best-effort int parse (digits only); ``default`` on failure."""
    if s is None:
        return default
    m = re.search(r"-?\d+", s)
    return int(m.group()) if m else default


def to_float(s: str | None, default: float | None = None) -> float | None:
    """Best-effort float parse; ``default`` on failure."""
    if s is None:
        return default
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else default
