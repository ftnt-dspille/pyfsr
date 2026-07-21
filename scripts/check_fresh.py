"""Level-1 catalog freshness probe — compare a cached compile catalog's
provenance against a live SOAR.

This was formerly ``pyfsr playbook check-fresh`` (and
``pyfsr.playbook_freshness``). The probe is a script concern, not SDK surface
— it reads ``fsr_playbooks._catalog_meta`` (another package's private
provenance table) — so it lives here as a runnable example rather than in the
installed package.

Exit codes (same as the old CLI): ``0`` fresh, ``2`` drift, ``1`` error /
unstamped.

Signals (cheap GETs, ~1.5 KB total):

- ``GET /api/version`` — FSR build (Tier-0: changes only on an upgrade).
- ``GET /api/publish/error`` → ``last_publish_time`` — appliance-wide publish
  watermark (Tier-1: any module/field/connector publish bumps it).
- ``GET /api/3/<coll>?$limit=0`` → ``hydra:totalItems`` — per-collection row
  count (catches add/delete drift, incl. picklist value adds with no publish).

Counts MISS in-place edits (rename a value, rebind a field's picklist) that
keep the row count constant — those need a Level-2 ETag re-pull, out of scope.

Usage::

    python scripts/check_fresh.py --server https://soar.example.com \\
        --user admin --password '...' --no-verify-ssl \\
        --db ~/.cache/pyfsr/fsr_reference.db

Connection args override ``FSR_*`` env vars (see :class:`pyfsr.config.EnvConfig`).
The catalog is the ``fsr_playbooks`` reference DB (packaged slim DB by default;
warm one against a live SOAR first — without a provenance stamp this exits 1).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Any

#: Collections we cheap-count, mapped to the catalog table whose drift they
#: signal. ``model_metadatas`` == published modules (equals staging when
#: there's no pending publish).
COUNT_COLLECTIONS: dict[str, str] = {
    "model_metadatas": "modules",
    "attribute_metadatas": "module_fields",
    "picklists": "picklists",
    "picklist_names": "picklists",
    "tags": "tags",
}


@dataclass
class FreshnessProbe:
    """Result of the Level-1 cheap probe against a live SOAR."""

    version: str | None = None
    last_publish_time: Any | None = None
    counts: dict[str, int | None] = field(default_factory=dict)


@dataclass
class FreshnessReport:
    """Outcome of comparing stamped provenance against a live probe."""

    instance_label: str = ""
    stored: dict[str, Any] = field(default_factory=dict)
    live: dict[str, Any] = field(default_factory=dict)
    drift: list[str] = field(default_factory=list)
    unstamped: bool = False

    @property
    def is_fresh(self) -> bool:
        """``True`` when the catalog is fully stamped and shows no drift."""
        return not self.unstamped and not self.drift


def _total_items(resp: object) -> int | None:
    if isinstance(resp, dict):
        v = resp.get("hydra:totalItems")
        if isinstance(v, int):
            return v
    return None


def probe_live(client) -> FreshnessProbe:
    """Run the Level-1 cheap probe against a live client. Best-effort: a failed
    signal is recorded as ``None`` rather than aborting the whole probe."""
    out = FreshnessProbe()
    try:
        v = client.get("/api/version")
        if isinstance(v, dict):
            out.version = v.get("version")
    except Exception:  # noqa: BLE001
        pass
    try:
        p = client.get("/api/publish/error")
        if isinstance(p, dict):
            out.last_publish_time = p.get("last_publish_time")
    except Exception:  # noqa: BLE001
        pass
    for coll in COUNT_COLLECTIONS:
        try:
            r = client.get(f"/api/3/{coll}?$limit=0")
            out.counts[coll] = _total_items(r)
        except Exception:  # noqa: BLE001
            out.counts[coll] = None
    return out


def compare(stored: dict, live: dict | FreshnessProbe) -> FreshnessReport:
    """Diff stamped provenance (``_catalog_meta`` key/value dict) against a live
    probe result. Returns a :class:`FreshnessReport`."""
    live_dict: dict = live.__dict__ if isinstance(live, FreshnessProbe) else live
    rep = FreshnessReport(
        instance_label=stored.get("instance_label", "") or "",
        stored=stored,
        live=live_dict,
    )
    if not stored.get("base_url_hash"):
        rep.unstamped = True
        return rep

    sv = stored.get("fsr_version")
    lv = live_dict.get("version")
    if sv and lv and sv != lv:
        rep.drift.append(f"FSR upgraded: catalog {sv} -> live {lv} (Tier-0 rebuild)")

    sp = stored.get("last_publish_time")
    lp = live_dict.get("last_publish_time")
    if sp is not None and lp is not None and str(sp) != str(lp):
        rep.drift.append(f"publish watermark advanced: {sp} -> {lp} (structural drift — re-warm Tier-1)")

    for coll, n in (live_dict.get("counts") or {}).items():
        s = stored.get(f"count:{coll}")
        if s is None or n is None:
            continue
        if str(s) != str(n):
            delta = n - int(s) if str(s).lstrip("-").isdigit() else "?"
            sign = f"{'+' if isinstance(delta, int) and delta >= 0 else ''}{delta}"
            rep.drift.append(f"{coll}: {s} -> {n} ({sign})")
    return rep


def _resolve_db(args: argparse.Namespace) -> str:
    """Resolve the reference catalog path: ``--db`` → ``$FSRPB_DB`` → packaged
    slim DB. Raises the same clear error as the compiler when the optional
    ``fsr_playbooks`` extra is absent."""
    from fsr_playbooks.catalog import default_db_path

    return args.db or os.environ.get("FSRPB_DB") or str(default_db_path())


def _make_client(args: argparse.Namespace):
    """Build a :class:`pyfsr.FortiSOAR` from ``FSR_*`` env plus CLI overrides."""
    overrides: dict[str, Any] = {}
    if args.server:
        overrides["base_url"] = args.server
    if args.token:
        overrides["auth"] = args.token
    elif args.username and args.password:
        overrides["auth"] = (args.username, args.password)
    if args.port is not None:
        overrides["port"] = args.port
    if args.no_verify_ssl:
        overrides["verify_ssl"] = False
        overrides["suppress_insecure_warnings"] = True

    # When a full connection is supplied via flags, don't require FSR_* env.
    if "base_url" in overrides and "auth" in overrides:
        from pyfsr import FortiSOAR

        return FortiSOAR(**overrides)
    from pyfsr.config import EnvConfig

    return EnvConfig.from_env().client(**overrides)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a cached compile catalog's provenance against a live SOAR.",
    )
    g = parser.add_argument_group("connection (overrides FSR_* env)")
    g.add_argument("--server", help="appliance host or URL (FSR_BASE_URL)")
    g.add_argument("--token", "--api-key", dest="token", help="API key (FSR_API_KEY)")
    g.add_argument("--username", help="login user (FSR_USERNAME)")
    g.add_argument("--password", help="login password (FSR_PASSWORD)")
    g.add_argument("--port", type=int, help="port override (FSR_PORT)")
    g.add_argument("--no-verify-ssl", action="store_true", help="disable TLS verification")
    parser.add_argument("--db", help="reference catalog path (default: packaged/dev DB)")
    args = parser.parse_args(argv)

    from fsr_playbooks import _catalog_meta

    db = _resolve_db(args)
    conn = sqlite3.connect(db)
    try:
        stored = _catalog_meta.get_all(conn)
    finally:
        conn.close()

    if not stored.get("base_url_hash"):
        print(
            f"catalog {db} carries no provenance stamp — run warmup against a target SOAR first.",
            file=sys.stderr,
        )
        return 1

    client = _make_client(args)
    live = probe_live(client)
    report = compare(stored, live)

    print(f"catalog      {db}", file=sys.stderr)
    print(f"instance     {report.instance_label or '(unlabeled)'}", file=sys.stderr)
    print(f"fsr_version  {stored.get('fsr_version')} -> {live.version}", file=sys.stderr)
    print(f"result       {'FRESH' if report.is_fresh else 'STALE'}", file=sys.stderr)

    if report.drift:
        print("drift detected:", file=sys.stderr)
        for line in report.drift:
            print(f"  - {line}", file=sys.stderr)
        print("re-run warmup against the target to refresh the catalog.", file=sys.stderr)
        return 2
    print("catalog is up to date with the live instance.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
