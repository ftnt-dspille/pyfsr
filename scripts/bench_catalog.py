#!/usr/bin/env python
"""Measure the playbook-compiler catalog warm (pyfsr.authoring) across instances.

Read-only against the boxes: it warms a throwaway cache directory (XDG_CACHE_HOME
points at a temp dir), never the real ~/.cache, and never writes to a box.

    uv run python scripts/bench_catalog.py --instance A --instance B [--json]

Per instance:
  cold        first warm into an empty cache (seed copy + every section)
  cached      warm again in the same process inside the max-age window
  new_client  a new client/process inside the window (disk only)
  stale       a warm after the max-age window has passed (sections due again)
With two instances: A -> B -> A, the time to be ready again on A, plus how many
catalog files exist after addressing one box by two spellings of its URL.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")


class _Counter:
    """Wrap a client's request method to count HTTP calls."""

    def __init__(self, client: Any) -> None:
        self.n = 0
        orig = client.request

        def counted(*a: Any, **kw: Any) -> Any:
            self.n += 1
            return orig(*a, **kw)

        client.request = counted


def _client(alias: str) -> Any:
    from pyfsr.instances import InstanceRegistry

    for attempt in range(4):  # sign-in on these boxes drops now and then under repeated logins
        try:
            c = InstanceRegistry.load().client(alias)
            c.get("/api/3/tags", params={"$limit": 1})  # authenticate outside the timings
            return c
        except Exception:
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


PLAYBOOK = """
name: Bench enrich alert
trigger: {type: manual, module: alerts}
steps:
  - id: lookup
    type: connector
    connector: fortinet-fortianalyzer
    operation: get_events
  - id: close
    type: update_record
    module: alerts
    fields:
      status: "{{ 'AlertStatus' | picklist('Closed') }}"
"""


def _warm(client: Any, playbook: str | None = None) -> tuple[float, int, Path]:
    from pyfsr import authoring

    counter = _Counter(client)
    t = time.perf_counter()
    path, _ = authoring._warm_cache(client, playbook=playbook)
    return round(time.perf_counter() - t, 2), counter.n, Path(path)


def bench_instance(alias: str) -> dict[str, Any]:
    from pyfsr import authoring

    out: dict[str, Any] = {"instance": alias}
    c = _client(alias)
    out["cold_s"], out["cold_calls"], db = _warm(c)
    out["db_mb"] = round(db.stat().st_size / 1048576, 1)
    out["cached_s"], out["cached_calls"], _ = _warm(c)
    out["new_client_s"], out["new_client_calls"], _ = _warm(_client(alias))

    # Past the window: max-age 0 makes every section due again (same as waiting it out).
    os.environ["PYFSR_CATALOG_MAX_AGE"] = "0.001"
    time.sleep(0.01)
    try:
        out["stale_s"], out["stale_calls"], _ = _warm(c)
    finally:
        os.environ.pop("PYFSR_CATALOG_MAX_AGE", None)
    out["max_age_s"] = authoring._catalog_max_age()
    out["leveled"] = authoring._library_box_catalog() is not None
    if out["leveled"]:
        out.update(_bench_levels(alias))
    return out


def _bench_levels(alias: str) -> dict[str, Any]:
    """Targeted (playbook) warms: cold box, inside and after the recheck window."""
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as tmp:
        prev = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = tmp
        try:
            c = _client(alias)
            out["pb_cold_s"], out["pb_cold_calls"], _ = _warm(c, PLAYBOOK)
            out["pb_again_s"], out["pb_again_calls"], _ = _warm(c, PLAYBOOK)
            os.environ["FSRPB_WARM_RECHECK"] = "0"
            try:
                out["pb_recheck_s"], out["pb_recheck_calls"], _ = _warm(c, PLAYBOOK)
            finally:
                os.environ.pop("FSRPB_WARM_RECHECK", None)
        finally:
            if prev is not None:
                os.environ["XDG_CACHE_HOME"] = prev
    return out


def bench_switch(a: str, b: str) -> dict[str, Any]:
    ca, cb = _client(a), _client(b)
    _warm(ca)
    _warm(cb)
    back_s, back_calls, _ = _warm(_client(a))
    return {"ready_again_on_a_s": back_s, "ready_again_on_a_calls": back_calls}


def url_variants(alias: str) -> dict[str, Any]:
    """Address one box by two spellings of its URL; count catalog files made."""
    from pyfsr import authoring

    c = _client(alias)
    base = c.base_url
    spellings = {base, base.replace("https://", "HTTPS://", 1) + "/"}
    paths = set()
    for url in spellings:
        c.base_url = url
        if authoring._library_box_catalog() is not None:
            from fsr_playbooks.warmup import box_catalog_path  # type: ignore[import-not-found, unused-ignore]

            paths.add(str(box_catalog_path(url)))
        else:
            paths.add(str(authoring._default_cache_db(c)))
    c.base_url = base
    return {"spellings": len(spellings), "catalog_files": len(paths)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--instance", action="append", required=True)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_CACHE_HOME"] = tmp
        os.environ.pop("PYFSR_CATALOG_MAX_AGE", None)
        out: dict[str, Any] = {"instances": [bench_instance(a) for a in args.instance]}
        if len(args.instance) >= 2:
            out["switch"] = bench_switch(args.instance[0], args.instance[1])
        out["url_variants"] = url_variants(args.instance[0])
    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    for r in out["instances"]:
        print(f"[{r['instance']}] catalog {r['db_mb']} MB, max-age {r['max_age_s']}s")
        for k in ("cold", "cached", "new_client", "stale"):
            print(f"  {k:<11} {r[f'{k}_s']:>6} s  {r[f'{k}_calls']:>4} HTTP calls")
        for k in ("pb_cold", "pb_again", "pb_recheck"):
            if f"{k}_s" in r:
                print(f"  {k:<11} {r[f'{k}_s']:>6} s  {r[f'{k}_calls']:>4} HTTP calls")
    if "switch" in out:
        s = out["switch"]
        print(f"[switch A->B->A] ready again on A: {s['ready_again_on_a_s']} s, {s['ready_again_on_a_calls']} calls")
    v = out["url_variants"]
    print(f"[url spellings] {v['spellings']} spellings of one box -> {v['catalog_files']} catalog file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
