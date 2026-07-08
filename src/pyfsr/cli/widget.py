"""``pyfsr widget`` command group — upload + publish widgets on a live appliance.

Every command prints the **target host** it acted on before doing anything
mutating, so a wrong-target deploy can't quietly look successful.

Subcommands:

- ``list [--installed] [--name N]`` — list widget records.
- ``upload <tgz> [--no-replace]`` — stage a widget in the dev workspace (not live).
- ``publish <uuid> [--as-draft] [--no-replace]`` — flip a staged widget live.
- ``deploy <tgz> [--no-replace] [--timeout N]`` — upload + publish + settle, the
  common path.
- ``export <uuid> <dest.tgz> [--development]`` — download a widget archive.
- ``rm <uuid>`` — delete a widget record.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from ..exceptions import WidgetError
from . import _output
from .playbook import _make_client, add_connection_args

if TYPE_CHECKING:
    from ..client import FortiSOAR


def _print_target(client: FortiSOAR) -> None:
    print(f"target: {client.base_url}", file=sys.stderr)


def cmd_list(args: argparse.Namespace) -> int:
    client = _make_client(args)
    records = client.widgets.list(installed=args.installed, name=args.name)
    headers = ["uuid", "name", "version", "draft", "installed"]
    rows = [[r.uuid, r.name, r.version, r.draft, r.installed] for r in records]
    _output.render(rows, headers, fmt=args.fmt)
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    client = _make_client(args)
    _print_target(client)
    try:
        record = client.widgets.upload(args.path, replace=not args.no_replace)
    except WidgetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"uploaded {record.name} {record.version} (uuid={record.uuid}, draft={record.draft})")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    client = _make_client(args)
    _print_target(client)
    uuid = args.uuid
    if not _looks_like_uuid(uuid):
        record = client.widgets.get(uuid)
        if record is None or not record.uuid:
            print(f"error: no widget found for name {uuid!r}", file=sys.stderr)
            return 1
        uuid = record.uuid
    try:
        record = client.widgets.publish(uuid, replace=not args.no_replace, go_live=not args.as_draft)
    except WidgetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"published {record.name} {record.version} (draft={record.draft}, installed={record.installed})")
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    client = _make_client(args)
    _print_target(client)
    try:
        record = client.widgets.deploy(
            args.path,
            replace=not args.no_replace,
            timeout=args.timeout,
        )
    except WidgetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"deployed {record.name} {record.version} (live={record.published})")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    client = _make_client(args)
    uuid = args.uuid
    if not _looks_like_uuid(uuid):
        record = client.widgets.get(uuid)
        if record is None or not record.uuid:
            print(f"error: no widget found for name {uuid!r}", file=sys.stderr)
            return 1
        uuid = record.uuid
    path = client.widgets.export(uuid, args.dest, development=args.development)
    print(path)
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    client = _make_client(args)
    _print_target(client)
    client.widgets.remove(args.uuid)
    print(f"removed {args.uuid}")
    return 0


def _looks_like_uuid(value: str) -> bool:
    return len(value) == 36 and value.count("-") == 4


def build_subparser(sub: argparse._SubParsersAction) -> None:
    """Wire the ``widget`` subcommands onto an existing subparsers object."""
    from .__main__ import _add_fmt

    p_list = sub.add_parser("list", help="list widget records")
    p_list.add_argument("--installed", action="store_true", default=None, help="only installed/published widgets")
    p_list.add_argument("--name", help="filter to this widget name")
    add_connection_args(p_list)
    _add_fmt(p_list)
    p_list.set_defaults(func=cmd_list)

    p_upload = sub.add_parser("upload", help="stage a widget .tgz in the dev workspace (not live)")
    p_upload.add_argument("path", help="path to the widget .tgz")
    p_upload.add_argument(
        "--no-replace",
        action="store_true",
        help="fail instead of overwriting an already-staged copy of this name+version",
    )
    add_connection_args(p_upload)
    p_upload.set_defaults(func=cmd_upload)

    p_publish = sub.add_parser("publish", help="flip a staged widget live")
    p_publish.add_argument("uuid", help="widget uuid, or a widget name to resolve its newest staged version")
    p_publish.add_argument("--as-draft", action="store_true", help="publish without going live (rarely wanted)")
    p_publish.add_argument("--no-replace", action="store_true", help="don't supersede the currently-installed version")
    add_connection_args(p_publish)
    p_publish.set_defaults(func=cmd_publish)

    p_deploy = sub.add_parser("deploy", help="upload + publish + settle in one call (the common path)")
    p_deploy.add_argument("path", help="path to the widget .tgz")
    p_deploy.add_argument("--no-replace", action="store_true", help="see upload/publish --no-replace")
    p_deploy.add_argument("--timeout", type=float, default=60.0, help="settle-poll timeout in seconds (default 60)")
    add_connection_args(p_deploy)
    p_deploy.set_defaults(func=cmd_deploy)

    p_export = sub.add_parser("export", help="download a widget archive")
    p_export.add_argument("uuid", help="widget uuid, or a widget name to resolve its newest version")
    p_export.add_argument("dest", help="destination .tgz path")
    p_export.add_argument("--development", action="store_true", help="export the dev-workspace copy, not installed")
    add_connection_args(p_export)
    p_export.set_defaults(func=cmd_export)

    p_rm = sub.add_parser("rm", help="delete a widget record")
    p_rm.add_argument("uuid", help="widget uuid")
    add_connection_args(p_rm)
    p_rm.set_defaults(func=cmd_rm)
