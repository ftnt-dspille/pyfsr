#!/usr/bin/env python
"""Measure the live schema cache (pyfsr.schema) against the fetch it replaced.

Read-only: it fetches metadata and runs pre-write checks, never writes a record.
Uses a throwaway cache directory, so the real ~/.cache/pyfsr is untouched.

    uv run python scripts/bench_schema.py --instance A [--instance B] [--checks 1000]

Per instance:
  full_fetch      the old path: every module's metadata (describe() and the
                  picklist field map each did this once per module per client)
  single_fetch    the new path: one module's metadata
  first_check     a client's first check of a module (one fetch + checking)
  warm_check      each later check in a loop (no network)
  disk_hit        a new client/process reusing the disk cache (no fetch)
With two instances, it also times switching A -> B -> A: switching back clears A's
disk cache and re-warms the modules A had cached, eagerly.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
import warnings
from typing import Any

warnings.filterwarnings("ignore")

MODULES = ["alerts", "comments", "indicators", "tasks"]
PAYLOAD = {"name": "bench", "description": "schema bench", "severity": "High"}


def _client(alias: str) -> Any:
    from pyfsr.instances import InstanceRegistry

    c = InstanceRegistry.load().client(alias)
    c.get("/api/3/model_metadatas", params={"type": "alerts", "$limit": 1})  # authenticate outside the timings
    return c


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def _timed(fn, repeat: int = 3) -> tuple[float, Any]:
    out, runs = None, []
    for _ in range(repeat):
        t = time.perf_counter()
        out = fn()
        runs.append(time.perf_counter() - t)
    return statistics.median(runs), out


def bench_instance(alias: str, checks: int) -> dict[str, Any]:
    from pyfsr.api.modules import _META_FULL
    from pyfsr.schema import SchemaCache

    c = _client(alias)
    full_s, full = _timed(lambda: c.get(_META_FULL))
    full_bytes = len(json.dumps(full))
    single_s, _ = _timed(lambda: SchemaCache(c)._fetch_one("alerts"))

    c.picklists.values("Severity")  # one-time bulk picklist load, timed separately
    t = time.perf_counter()
    c.picklists.clear_cache()
    c.picklists.values("Severity")
    picklist_s = time.perf_counter() - t

    c.schema = SchemaCache(c)
    t = time.perf_counter()
    first = c.schema.check("alerts", PAYLOAD)
    first_s = time.perf_counter() - t
    fetches_after_first = c.schema.fetches

    t = time.perf_counter()
    for _ in range(checks):
        c.schema.check("alerts", PAYLOAD)
    loop_s = time.perf_counter() - t

    other = SchemaCache(c)  # what a second process sees: disk only
    t = time.perf_counter()
    other.get("alerts")
    disk_s = time.perf_counter() - t

    return {
        "instance": alias,
        "version": str(c.version()) if not isinstance(c.version(), dict) else c.version().get("version"),
        "full_fetch_ms": _ms(full_s),
        "full_fetch_kb": round(full_bytes / 1024),
        "single_fetch_ms": _ms(single_s),
        "picklist_load_ms": _ms(picklist_s),
        "first_check_ms": _ms(first_s),
        "first_check_fetches": fetches_after_first,
        "first_check_ok": first.ok,
        "warm_checks": checks,
        "warm_check_us": round(loop_s / checks * 1e6, 1),
        "warm_loop_fetches": c.schema.fetches - fetches_after_first,
        "disk_hit_ms": _ms(disk_s),
        "disk_hit_fetches": other.fetches,
        # old path per client: describe() + picklist field map, each a full fetch
        "old_cold_per_module_ms": _ms(2 * full_s),
    }


def bench_switch(a: str, b: str) -> dict[str, Any]:
    from pyfsr.schema import SchemaCache

    ca, cb = _client(a), _client(b)
    ca.schema = SchemaCache(ca)
    t = time.perf_counter()
    ca.schema.warm(MODULES)
    warm_a = time.perf_counter() - t

    cb.schema = SchemaCache(cb)
    t = time.perf_counter()
    cb.schema.get("alerts")  # switch to B
    switch_b = time.perf_counter() - t

    back = SchemaCache(ca)  # first use of A after B: clear + eager re-warm
    t = time.perf_counter()
    back.get("alerts")
    switch_back = time.perf_counter() - t
    rewarmed = back.cached_modules()
    t = time.perf_counter()
    for m in MODULES:
        back.get(m)
    after = time.perf_counter() - t

    stay = SchemaCache(ca)  # same box again: no clear, disk hit
    t = time.perf_counter()
    for m in MODULES:
        stay.get(m)
    stay_s = time.perf_counter() - t

    return {
        "modules": MODULES,
        "warm_a_ms": _ms(warm_a),
        "switch_to_b_ms": _ms(switch_b),
        "switch_back_to_a_ms": _ms(switch_back),
        "switch_back_fetches": back.fetches,
        "rewarmed": rewarmed,
        "after_rewarm_gets_ms": _ms(after),
        "after_rewarm_extra_fetches": back.fetches - len(MODULES),
        "same_box_again_ms": _ms(stay_s),
        "same_box_again_fetches": stay.fetches,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--instance", action="append", required=True, help="registry alias (repeat for a second box)")
    p.add_argument("--checks", type=int, default=1000)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["PYFSR_SCHEMA_CACHE_DIR"] = tmp
        os.environ.setdefault("PYFSR_SCHEMA_TTL", "600")
        out: dict[str, Any] = {"instances": [bench_instance(a, args.checks) for a in args.instance]}
        if len(args.instance) >= 2:
            out["switch"] = bench_switch(args.instance[0], args.instance[1])

    if args.json:
        print(json.dumps(out, indent=2))
        return 0
    for r in out["instances"]:
        print(f"[{r['instance']}] FortiSOAR {r['version']}")
        print(
            f"  old: full metadata fetch      {r['full_fetch_ms']:>8} ms  "
            f"({r['full_fetch_kb']} KB); x2 per module per client"
        )
        print(f"  new: one-module fetch         {r['single_fetch_ms']:>8} ms")
        print(f"  first check of a module       {r['first_check_ms']:>8} ms  ({r['first_check_fetches']} fetch)")
        print(f"  one-time picklist load        {r['picklist_load_ms']:>8} ms  (also paid by the old path)")
        print(
            f"  warm check, x{r['warm_checks']:<6}         {r['warm_check_us']:>8} us each  "
            f"({r['warm_loop_fetches']} fetches)"
        )
        print(f"  new process, disk hit         {r['disk_hit_ms']:>8} ms  ({r['disk_hit_fetches']} fetches)")
    s = out.get("switch")
    if s:
        print(
            f"[switch {args.instance[0]} -> {args.instance[1]} -> {args.instance[0]}] "
            f"modules: {', '.join(s['modules'])}"
        )
        print(f"  warm A                        {s['warm_a_ms']:>8} ms")
        print(f"  switch to B (first get)       {s['switch_to_b_ms']:>8} ms")
        print(
            f"  switch back to A: clear+rewarm {s['switch_back_to_a_ms']:>7} ms  "
            f"({s['switch_back_fetches']} fetches; rewarmed {len(s['rewarmed'])})"
        )
        print(
            f"  then {len(s['modules'])} gets on A             {s['after_rewarm_gets_ms']:>8} ms  "
            f"({s['after_rewarm_extra_fetches']} extra fetches)"
        )
        print(
            f"  same box again (no switch)    {s['same_box_again_ms']:>8} ms  ({s['same_box_again_fetches']} fetches)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
