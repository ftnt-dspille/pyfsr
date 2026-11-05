"""``pyfsr pack`` command group — assemble a solution pack from live content
and bundled installers, without hand-knowing the wire formats.

Subcommands:

- ``init <dir> --name N --label L --version V`` — scaffold a pack.
- ``add-connector <tgz> --pack <dir>`` / ``add-widget <tgz> --pack <dir>`` —
  bundle an installer (data.json row from the tgz's own info.json + file copy).
- ``add-dashboard <uuid|name> --pack <dir>`` — export a dashboard from the
  live appliance into the pack.
- ``add-records <module> --pack <dir> (--uuid U | --filter-json J) [--limit N]``
  — export records as a record set (the query always carries a limit; the
  export engine emits nothing without one).
- ``add-playbooks <collection> --pack <dir>`` — export a playbook collection.
- ``soc-demo <module> <uuid> --pack <dir> --title T --collection C`` — the
  SOC-simulator demo pattern end to end: generate + deploy an abstract-trigger
  playbook replaying the record through create_simulated_alert, ship the
  collection into the pack, and add a scenario recordSet that runs it.
- ``build <dir> [--out zip]`` — sync contents from the directories, zip in the
  appliance exporter's layout, validate.

Bundled-installer packs install ONLY via the Import Wizard path
(``import_config.import_file``); the Content Hub path silently installs none.
"""

from __future__ import annotations

import argparse
import functools
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..exceptions import FortiSOARException
from ..utils import packtools
from .playbook import _make_client, add_connection_args

if TYPE_CHECKING:
    from ..client import FortiSOAR


def _print_target(client: FortiSOAR) -> None:
    print(f"target: {client.base_url}", file=sys.stderr)


def _guarded(
    fn: Callable[[argparse.Namespace], int],
) -> Callable[[argparse.Namespace], int]:
    """Turn toolkit/transport errors into a one-line error + exit 1."""

    @functools.wraps(fn)
    def wrapper(args: argparse.Namespace) -> int:
        try:
            return fn(args)
        except (packtools.PackError, FortiSOARException, ValueError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    return wrapper


@_guarded
def cmd_init(args: argparse.Namespace) -> int:
    pack = packtools.Pack.init(
        args.directory,
        name=args.name,
        label=args.label,
        version=args.version,
        description=args.description or "",
        publisher=args.publisher or "",
    )
    print(f"initialized {pack.path} ({args.name} {args.version})")
    return 0


@_guarded
def cmd_add_installer(args: argparse.Namespace) -> int:
    pack = packtools.Pack(args.pack)
    row = pack.add_installer(args.category, args.tgz)
    pack.sync_contents()
    pack.save()
    print(f"bundled {row['name']} {row.get('version')} into {args.category}/ (installer_path={row['installer_path']})")
    return 0


@_guarded
def cmd_add_dashboard(args: argparse.Namespace) -> int:
    client = _make_client(args)
    _print_target(client)
    pack = packtools.Pack(args.pack)
    result = packtools.add_dashboard(client, pack, args.dashboard)
    pack.save()
    print(f"added dashboard {result['name']!r} ({result['files']} file(s))")
    return 0


@_guarded
def cmd_add_records(args: argparse.Namespace) -> int:
    client = _make_client(args)
    _print_target(client)
    pack = packtools.Pack(args.pack)
    result = packtools.add_records(
        client,
        pack,
        args.module,
        record_uuid=args.uuid,
        filter_json=args.filter_json,
        limit=args.limit,
    )
    pack.save()
    print(f"added record set for {result['module']} ({result['files']} file(s), limit={result['limit']})")
    return 0


@_guarded
def cmd_add_playbooks(args: argparse.Namespace) -> int:
    client = _make_client(args)
    _print_target(client)
    pack = packtools.Pack(args.pack)
    result = packtools.add_playbook_collection(client, pack, args.collection)
    pack.save()
    print(f"added playbook collection {result['collection']!r} ({result['files']} file(s))")
    return 0


@_guarded
def cmd_soc_demo(args: argparse.Namespace) -> int:
    client = _make_client(args)
    _print_target(client)
    pack = packtools.Pack(args.pack)
    result = packtools.soc_demo(
        client,
        pack,
        args.module,
        args.uuid,
        title=args.title,
        collection=args.collection,
        playbook_name=args.playbook_name,
        yaml_out=args.yaml_out,
        fields_to_ignore=args.fields_to_ignore,
        deploy=not args.no_deploy,
    )
    pack.save()
    print(f"playbook : {result['playbook']} (uuid={result['workflow_uuid']}, deployed={result['deployed']})")
    print(f"yaml     : {result['yaml']}")
    print(
        f"scenario : {result['scenario_title']!r} -> records/scenario/demo.json "
        f"(playbooks in pack: {result['playbooks_in_pack']} file(s))"
    )
    return 0


@_guarded
def cmd_build(args: argparse.Namespace) -> int:
    pack = packtools.Pack(args.directory)
    zip_path = pack.build(args.out, strict=not args.no_strict)
    print(f"built {zip_path}")
    return 0


def build_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("init", help="scaffold a solution pack directory")
    p.add_argument("directory")
    p.add_argument("--name", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--description")
    p.add_argument("--publisher")
    p.set_defaults(func=cmd_init)

    for category, help_text in (("connector", "bundle a connector tgz"), ("widget", "bundle a widget tgz")):
        p = sub.add_parser(f"add-{category}", help=help_text)
        p.add_argument("tgz")
        p.add_argument("--pack", required=True)
        p.set_defaults(func=cmd_add_installer, category=category + "s")

    p = sub.add_parser("add-dashboard", help="export a dashboard from the appliance into the pack")
    p.add_argument("dashboard", help="dashboard uuid or exact displayName")
    p.add_argument("--pack", required=True)
    add_connection_args(p)
    p.set_defaults(func=cmd_add_dashboard)

    p = sub.add_parser("add-records", help="export records as a record set")
    p.add_argument("module")
    p.add_argument("--pack", required=True)
    p.add_argument("--uuid", help="export exactly this record")
    p.add_argument("--filter-json", help="raw query body for the record set")
    p.add_argument(
        "--limit", type=int, default=100, help="record-set limit (the engine emits nothing without one; default 100)"
    )
    add_connection_args(p)
    p.set_defaults(func=cmd_add_records)

    p = sub.add_parser("add-playbooks", help="export a playbook collection into the pack")
    p.add_argument("collection")
    p.add_argument("--pack", required=True)
    add_connection_args(p)
    p.set_defaults(func=cmd_add_playbooks)

    p = sub.add_parser("soc-demo", help="generate the SOC-simulator demo playbook + scenario record")
    p.add_argument("module")
    p.add_argument("uuid", help="the live record to replay (e.g. the captured alert)")
    p.add_argument("--pack", required=True)
    p.add_argument("--title", required=True, help="scenario title (what the user clicks Run on)")
    p.add_argument("--collection", required=True, help="playbook collection name for the pack")
    p.add_argument("--playbook-name", help="default: 'Create Simulated <Module> Record'")
    p.add_argument("--yaml-out", help="where to write the generated playbook YAML (default: next to the pack)")
    p.add_argument("--fields-to-ignore", default=packtools.DEFAULT_SIMULATED_IGNORE)
    p.add_argument("--no-deploy", action="store_true", help="generate + compile only; do not deploy")
    add_connection_args(p)
    p.set_defaults(func=cmd_soc_demo)

    p = sub.add_parser("build", help="sync contents, zip, and validate the pack")
    p.add_argument("directory")
    p.add_argument("--out")
    p.add_argument("--no-strict", action="store_true", help="report findings but do not fail the build")
    p.set_defaults(func=cmd_build)
