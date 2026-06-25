#!/usr/bin/env python3
"""Pre-commit gate: no lab-box IPs or default passwords leak into the repo.

Two leak classes are refused:

1. **Lab IPs** -- any ``10.99.<octet>.<octet>`` literal. The FortiSOAR lab
   appliances live on the ``10.99.x.x`` subnet; those IPs aren't secrets but
   identify an internal lab host and must not ship publicly (examples,
   docstrings, provenance constants, validation docs).
2. **Default passwords** -- ``fortinet`` (the default FortiSOAR ``csadmin``
   password) used as a credential *value*. Matched only in credential context
   (``password=fortinet`` / ``--password <redacted>`` / ``FSR_PASSWORD=<redacted>``
   / ``"password": "fortinet"``), so the company name, the connector
   ``fortinet-fortisiem``, ``dspille@fortinet.com``, and ``Fortinet's repo``
   are NOT flagged -- only the bare word used as a secret value.

Run as a pre-commit hook it scans every tracked file (``pass_filenames: false``
+ ``always_run``), so a partial commit can't sneak a leak through by simply
not staging the offending file. Run directly (``uv run python
scripts/check_no_lab_ips.py``) to scan the whole tree -- the "is main clean?"
check before a release.

Exit 0 when clean, 1 on any violation.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

#: Lab-box IPv4 range (``10.99.x.x``). Full four-octet form only, so a bare
#: ``10.99`` (e.g. a version fragment) isn't a hit.
_LAB_IP = re.compile(r"10\.99\.\d{1,3}\.\d{1,3}")

#: ``fortinet`` as a credential *value*. Requires a credential keyword
#: (password / passwd / secret / token / api_key) on the same line, with only
#: quotes / spaces / ``:`` / ``=`` between it and ``fortinet``. This excludes
#: the company name, the ``fortinet-fortisiem`` connector, and the
#: ``fortinet.com`` domain -- none of those sit behind a password keyword.
_FORTINET_PASS = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\b[\"'\s:=]*\bfortinet\b")

#: (pattern, human label) -- order matters only for the violation message.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_LAB_IP, "lab 10.99.x.x IP"),
    (_FORTINET_PASS, "default 'fortinet' password"),
]

#: This file itself documents the patterns -- skip it so the gate doesn't flag
#: its own docstrings/comments.
_SELF = Path(__file__).resolve()


def _tracked_files() -> list[Path]:
    """All git-tracked files (the set a release would ship)."""
    out = subprocess.check_output(["git", "ls-files"], text=True, stderr=subprocess.DEVNULL)
    return [Path(line) for line in out.splitlines() if line]


def _scan(path: Path) -> list[tuple[int, str, str]]:
    """Return ``(line_no, label, line)`` for each pattern hit in ``path``."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return []
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for pattern, label in _PATTERNS:
            if pattern.search(line):
                hits.append((i, label, line.rstrip()))
                break  # one violation per line is enough; don't double-report
    return hits


def main(argv: list[str]) -> int:
    # Pre-commit passes staged filenames when pass_filenames is true; with the
    # always_run + pass_filenames:false config it passes none and we scan the
    # whole tracked tree. ``--all`` forces the full scan either way.
    if "--all" in argv:
        targets = _tracked_files()
    elif argv:
        targets = [Path(a) for a in argv if a != "--"]
    else:
        targets = _tracked_files()

    violations: list[str] = []
    for path in targets:
        if path.resolve() == _SELF:
            continue
        if not path.is_file():
            continue
        for line_no, label, line in _scan(path):
            violations.append(f"{path}:{line_no}: [{label}] {line}")

    if violations:
        print(
            "Refusing to commit: lab IP / default-password leak(s) found in tracked files.\n"
            "  These identify an internal lab host / default credential and must not ship publicly.\n"
            "  Sanitize to a placeholder (e.g. 'fortisoar.example.com', '<your-password>'):\n",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
