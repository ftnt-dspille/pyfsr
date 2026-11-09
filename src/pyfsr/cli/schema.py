"""``pyfsr schema`` -- the live module-schema cache behind every pre-write check.

::

    pyfsr schema show alerts --instance lab          # flags + fields (ownable, required, max length ...)
    pyfsr schema warm alerts comments --instance lab # fetch now; later writes and processes reuse it
    pyfsr schema check alerts payload.json --instance lab [--update]
    pyfsr schema status                              # what is cached, per instance
    pyfsr schema clear [--instance lab | --all]      # drop cached schemas

The cache lives under ``~/.cache/pyfsr/schema/<instance>/`` for ``PYFSR_SCHEMA_TTL``
seconds (default 600). Switching to another instance clears and re-warms that
box's cache automatically; ``clear`` is for when you know a box changed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from ..schema import _cache_root, _ttl, clear_disk
from .ai_eval import _client


def cmd_show(args: argparse.Namespace) -> int:
    client = _client(args)
    s = client.schema.get(args.module, refresh=args.refresh)
    if s is None:
        print(f"module {args.module!r} is not published on {client.base_url}", file=sys.stderr)
        return 1
    if args.format == "json":
        print(s.model_dump_json(indent=2))
        return 0
    flags = [n for n in ("ownable", "user_ownable", "queueable", "taggable") if getattr(s, n)]
    print(f"{s.type}  (collection {s.module}; {len(s.fields)} fields; flags: {', '.join(flags) or 'none'})")
    if not s.ownable:
        print("  not team-ownable: 'owners' in a write would be dropped")
    for cols in s.unique_constraints:
        print(f"  unique together: {', '.join(cols)}")
    print(f"  {'FIELD':<28} {'FORM':<20} {'REQ':<4} {'MAX':<6} NOTES")
    for f in sorted(s.fields.values(), key=lambda f: f.name):
        notes = []
        if f.picklist:
            notes.append(f"picklist {f.picklist}")
        if f.target:
            notes.append(f"-> {f.target}")
        if not f.writeable:
            notes.append("read-only")
        if f.unique:
            notes.append("unique")
        print(
            f"  {f.name:<28} {str(f.form_type or f.type):<20} {'yes' if f.required else '':<4} "
            f"{str(f.max_length or ''):<6} {'; '.join(notes)}"
        )
    return 0


def cmd_warm(args: argparse.Namespace) -> int:
    client = _client(args)
    t = time.time()
    found = client.schema.warm(args.modules, refresh=args.refresh)
    took = time.time() - t
    for m, ok in found.items():
        print(f"  {'ok ' if ok else 'missing'} {m}")
    print(f"{sum(found.values())}/{len(found)} modules in {took:.2f}s ({client.schema.fetches} fetches)")
    return 0 if all(found.values()) else 1


def cmd_check(args: argparse.Namespace) -> int:
    client = _client(args)
    raw = sys.stdin.read() if args.payload == "-" else Path(args.payload).read_text()
    data: Any = json.loads(raw)
    rows = data if isinstance(data, list) else [data]
    bad = 0
    for i, row in enumerate(rows):
        r = client.schema.check(args.module, row, op="update" if args.update else "create")
        label = f"[{i}] " if len(rows) > 1 else ""
        if r.ok:
            print(f"{label}ok" + (f"  (converts: {'; '.join(r.conversions)})" if r.conversions else ""))
        else:
            bad += 1
            for p in r.problems:
                print(f"{label}{p.code:<14} {p.message}")
    return 1 if bad else 0


def cmd_status(args: argparse.Namespace) -> int:
    root = _cache_root()
    ttl = _ttl()
    print(f"cache: {root}  (ttl {ttl:.0f}s{', disk layer off' if ttl <= 0 else ''})")
    try:
        active = (root / "active").read_text().strip()
    except OSError:
        active = ""
    dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    if not dirs:
        print("  nothing cached")
    now = time.time()
    for d in dirs:
        try:
            url = json.loads((d / "manifest.json").read_text()).get("base_url", "?")
        except (OSError, ValueError):
            url = "?"
        files = [p for p in d.glob("*.json") if p.name != "manifest.json"]
        fresh = sum(1 for p in files if now - p.stat().st_mtime <= ttl)
        mark = "*" if d.name == active else " "
        print(f" {mark} {url}  {fresh}/{len(files)} fresh: {', '.join(sorted(p.stem for p in files)) or '-'}")
    return 0


def cmd_clear(args: argparse.Namespace) -> int:
    if args.all:
        n = clear_disk(None)
        print(f"cleared {n} cached module schemas (all instances)")
        return 0
    client = _client(args)
    n = client.schema.clear()
    print(f"cleared {n} cached module schemas for {client.base_url}")
    return 0


def _common(p: argparse.ArgumentParser) -> None:
    from .playbook import add_connection_args

    add_connection_args(p)
    p.add_argument("--instance", help="alias from ~/.pyfsr/instances.toml (overrides the connection flags)")


def build_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("show", help="one module's live flags and fields (ownable, required, max length, picklists)")
    p.add_argument("module")
    p.add_argument("--refresh", action="store_true", help="fetch from the box even if cached")
    p.add_argument("--format", choices=["text", "json"], default="text")
    _common(p)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("warm", help="fetch module schemas now so later writes (and other processes) reuse them")
    p.add_argument("modules", nargs="+")
    p.add_argument("--refresh", action="store_true", help="refetch even if cached")
    _common(p)
    p.set_defaults(func=cmd_warm)

    p = sub.add_parser("check", help="dry-run a payload (JSON object or list; '-' for stdin) against the live schema")
    p.add_argument("module")
    p.add_argument("payload")
    p.add_argument("--update", action="store_true", help="check as a partial update (no required-field check)")
    _common(p)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("status", help="what is cached on disk, per instance (no network)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("clear", help="drop cached schemas for one instance, or --all")
    p.add_argument("--all", action="store_true", help="every instance (no connection needed)")
    _common(p)
    p.set_defaults(func=cmd_clear)
