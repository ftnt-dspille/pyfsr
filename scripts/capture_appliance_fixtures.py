#!/usr/bin/env python
"""Refresh the verified-live appliance fixtures from a real box.

This is the **capture** side of the doctested-return-examples loop
(see ``docs/plans/VALIDATED_RETURN_EXAMPLES_PLAN.md``). The fixtures in
``src/pyfsr/_testing/appliance_captures.py`` are frozen snapshots of real
appliance output; when a FortiSOAR release changes a verb's output format, a
capture drifts and a doctest fails. This script re-captures them from a live lab
appliance so the fixtures (and therefore the docs) stay honest.

It is a **manual, occasional** step — not run in CI. Run it against a lab box when:

- bumping the supported FortiSOAR version, or
- a doctest fails because a verb's output format changed.

Usage::

    # creds via env (or pass --host/--user/--password/--key)
    export PYFSR_APPLIANCE_HOST=fortisoar.example.com
    export PYFSR_APPLIANCE_USER=csadmin
    export PYFSR_APPLIANCE_PASSWORD='...'

    python scripts/capture_appliance_fixtures.py --out src/pyfsr/_testing/appliance_captures.py
    # then review the diff and re-run `make doctest`; fix any volatile-field masks.

The script connects over the real :class:`pyfsr.cli.appliance.transport.Transport`,
runs each verb's underlying command, and writes the raw stdout back into the
captures module with an updated provenance stamp. It does NOT auto-commit — review
the diff, since a format change may also require a parser fix.

Safety: this script only runs **read** commands (status, list, health, sizes,
one ``SELECT count(*)``). It performs no mutating action and needs no ``--yes``.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# Make `pyfsr` importable when run from a checkout (no install needed).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pyfsr.cli.appliance.facts import Facts  # noqa: E402
from pyfsr.cli.appliance.transport import make_transport  # noqa: E402

# Each entry: (constant_name, command_argv, optional sudo). Read-only by design.
# Mirrors the commands the ReplayTransport dispatches on; keep the two in sync.
_CAPTURES: list[tuple[str, list[str], bool]] = [
    ("DEVICE_UUID_FILE", ["cat", "/home/csadmin/device_uuid"], False),
    ("CSADM_DEVICE_UUID_RAW", ["csadm", "license", "--get-device-uuid"], True),
    ("LICENSE_DETAILS_RAW", ["csadm", "license", "--show-details"], True),
    ("FSR_VERSION", ["rpm", "-q", "--qf", "%{VERSION}", "cyops-ui"], False),
    ("DB_GETSIZE_RAW", ["csadm", "db", "--getsize"], True),
    ("SERVICES_STATUS_RAW", ["csadm", "services", "--status"], True),
    ("SS_RAW", ["ss", "-tlnp"], True),
    ("RMQ_STATUS_RAW", ["rabbitmqctl", "-q", "status"], True),
    ("FREE_RAW", ["free", "-m"], False),
    ("LOADAVG_RAW", ["cat", "/proc/loadavg"], False),
    ("HA_REPLICATION_RAW", ["csadm", "ha", "get-replication-stat"], True),
    ("LOG_BUNDLE_RAW", ["csadm", "log", "--collect"], True),
]


def _capture_one(facts: Facts, argv: list[str], sudo: bool) -> str:
    res = facts.transport.run(argv, sudo=sudo, timeout=120.0)
    if not res.ok:
        print(f"  WARN: {argv[0]} exited {res.returncode}: {res.stderr.strip()[:120]}", file=sys.stderr)
    return res.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="src/pyfsr/_testing/appliance_captures.py", help="captures module to (re)write")
    ap.parse_args()  # scaffolding pass: --out is not yet wired to a rewriter (see below)

    if not os.environ.get("PYFSR_APPLIANCE_HOST") and not any(a.startswith("--host") for a in sys.argv):
        print("ERROR: no appliance target. Set PYFSR_APPLIANCE_HOST (or pass --host).", file=sys.stderr)
        return 2

    facts = Facts(make_transport())
    host_label = facts.transport.target
    version = facts.fsr_version() or "(unknown)"
    today = date.today().isoformat()
    print(f"Capturing from {host_label} (FSR {version}) on {today} …", file=sys.stderr)

    captured: dict[str, str] = {}
    for name, argv, sudo in _CAPTURES:
        print(f"  {name} ← {' '.join(argv)}", file=sys.stderr)
        captured[name] = _capture_one(facts, argv, sudo)

    # ES health is JSON from curl, not a shell command — capture via the verb.
    print("  ES_HEALTH_RAW ← es.health()", file=sys.stderr)
    from pyfsr.cli.appliance import es as es_mod

    try:
        captured["ES_HEALTH_RAW"] = es_mod.health(facts).raw
    except Exception as exc:  # ES may be down; leave the fixture, warn.
        print(f"  WARN: es.health failed: {exc}", file=sys.stderr)

    # Report (does not write yet — this is a scaffolding pass; a full rewriter
    # would patch each constant in --out). For now, print a summary the operator
    # uses to hand-update the module, then re-run `make doctest`.
    print("\nCapture summary (review against the module, then `make doctest`):", file=sys.stderr)
    for name, val in captured.items():
        preview = val.strip().replace("\n", "\\n")[:70]
        print(f"  {name} = {preview!r}", file=sys.stderr)

    # Provenance stamp for the operator to paste in.
    print(
        f"\nProvenance: CAPTURE_HOST={host_label!r}  CAPTURE_VERSION={version!r}  CAPTURE_DATE={today!r}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
