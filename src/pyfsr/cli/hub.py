"""``pyfsr hub`` -- Content Hub updates for installed packs, connectors and widgets.

::

    pyfsr hub updates --instance lab                       # what has a newer version (GUI "Update available")
    pyfsr hub updates --type connector --format json --instance lab
    pyfsr hub update "SOAR Framework" servicenow --instance lab
    pyfsr hub update --all --type widget --instance lab --dry-run
    pyfsr hub update --all --instance lab --yes            # packs (dependency order), then connectors, widgets

Updates use the same call as the Content Hub *Update* button
(``POST /api/3/solutionpacks/update``). No backups are taken; for a connector the result
shows its configuration count before and after. ``--all`` asks for
confirmation unless ``--yes``.
"""

from __future__ import annotations

import argparse
import json
import sys

from ..api.content_hub import UPDATABLE_TYPES, ContentUpdateError
from .ai_eval import _client


def _types(args: argparse.Namespace) -> list[str] | None:
    return args.type or None


def cmd_updates(args: argparse.Namespace) -> int:
    client = _client(args)
    ups = client.content_hub.updates(_types(args), args.search or "", include_development=args.include_development)
    if args.format == "json":
        print(json.dumps([u.model_dump() for u in ups], indent=2))
        return 0
    if not ups:
        print("everything is up to date")
        return 0
    print(f"  {'TYPE':<13} {'NAME':<44} {'INSTALLED':<10} {'LATEST':<10} NOTES")
    for u in ups:
        notes = []
        if u.depends_on:
            notes.append("needs " + ", ".join(u.depends_on))
        if u.min_fsr_version:
            notes.append(f"FortiSOAR >= {u.min_fsr_version}")
        print(
            f"  {u.type:<13} {(u.label or u.name)[:44]:<44} {u.installed_version or '?':<10} "
            f"{u.latest_version:<10} {'; '.join(notes)}"
        )
    print(f"{len(ups)} updates available (listed in the order --all applies them)")
    return 0


def _print_result(r) -> None:  # type: ignore[no-untyped-def]
    extra = []
    if r.seconds is not None:
        extra.append(f"{r.seconds}s")
    if r.configs_before:
        extra.append(f"configs {r.configs_before}->{r.configs_after}")
    if r.message:
        extra.append(r.message)
    print(f"  {r.status:<8} {r.type:<13} {r.name:<40} {r.from_version} -> {r.to_version}  {'; '.join(extra)}")


def cmd_update(args: argparse.Namespace) -> int:
    if not args.all and not args.names:
        print("name one or more items, or pass --all", file=sys.stderr)
        return 2
    client = _client(args)
    hub = client.content_hub
    kwargs = {
        "check_compatibility": not args.ignore_compatibility,
        "timeout": args.timeout,
    }
    plan = hub.update_all(_types(args), names=args.names or None, dry_run=True)
    if not plan:
        print("nothing to update")
        return 0
    if args.names:
        found = {p.name.lower() for p in plan}
        missing = [n for n in args.names if n.lower() not in found]
        labels = {u.label.lower() for u in hub.updates(_types(args)) if u.label}
        missing = [n for n in missing if n.lower() not in labels]
        for n in missing:
            print(f"  no update available for {n!r}", file=sys.stderr)
    print("plan:")
    for p in plan:
        _print_result(p)
    if args.dry_run:
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("refusing to update without --yes (stdin is not a terminal)", file=sys.stderr)
            return 2
        if input(f"apply {len(plan)} update(s) on {client.base_url}? [y/N] ").strip().lower() not in ("y", "yes"):
            print("cancelled")
            return 1
    try:
        results = hub.update_all(_types(args), names=args.names or None, stop_on_error=args.stop_on_error, **kwargs)
    except ContentUpdateError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print("results:")
    for r in results:
        _print_result(r)
    failed = [r for r in results if not r.ok]
    print(f"{len(results) - len(failed)} updated, {len(failed)} failed or skipped")
    return 1 if failed else 0


def _common(p: argparse.ArgumentParser) -> None:
    from .playbook import add_connection_args

    add_connection_args(p)
    p.add_argument("--instance", help="alias from ~/.pyfsr/instances.toml (overrides the connection flags)")
    p.add_argument(
        "--type",
        action="append",
        choices=list(UPDATABLE_TYPES),
        help="limit to a content type (repeatable; default: all)",
    )


def build_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("updates", help="installed packs/connectors/widgets with a newer version available")
    p.add_argument("--search", help="name/label/description filter")
    p.add_argument("--include-development", action="store_true", help="include development connectors")
    p.add_argument("--format", choices=["text", "json"], default="text")
    _common(p)
    p.set_defaults(func=cmd_updates)

    p = sub.add_parser("update", help="install available updates (by name/label, or --all)")
    p.add_argument("names", nargs="*", help="item names or labels")
    p.add_argument("--all", action="store_true", help="every available update (of --type, if given)")
    p.add_argument("--dry-run", action="store_true", help="show the plan and stop")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--stop-on-error", action="store_true", help="stop at the first failed or refused item")
    p.add_argument("--ignore-compatibility", action="store_true", help="skip the FortiSOAR-version check")
    p.add_argument("--timeout", type=float, default=900.0, help="seconds to wait per item (default 900)")
    _common(p)
    p.set_defaults(func=cmd_update)
