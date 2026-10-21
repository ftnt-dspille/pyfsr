"""``pyfsr connector`` command group -- the local ConnectorsV2 dev loop.

Subcommands:

- ``scaffold <name> [--dir DIR]`` -- write a minimal, installable connector skeleton
  (info.json, connector.py, operations.py, requirements.txt). See
  :mod:`pyfsr.connector_dev`.
- ``package [DIR]`` -- build ``dist/<name>-<version>.tgz`` from a checkout
  (git-tracked files preferred; tests and packaging furniture excluded).
- ``deploy <DIR|TGZ>`` -- package if needed, install on the appliance from the
  ``FSR_*`` environment (``--env-file`` to load one), wait for activation,
  optionally ``--config-json`` a default configuration, and healthcheck.
- ``icon <svg> --connector DIR`` -- build the connector's large (80x80) and small (32x32)
  icon PNGs from an SVG (e.g. one from https://icons.fortinet.com), laid out like the
  stock Fortinet connector icons. See :mod:`pyfsr.connector_icon`.
"""

from __future__ import annotations

import argparse
import json
import sys


def cmd_icon(args: argparse.Namespace) -> int:
    from ..connector_icon import make_connector_icons

    try:
        written = make_connector_icons(
            args.svg,
            args.connector,
            wordmark_from=args.wordmark_from,
            wordmark=not args.no_wordmark,
        )
    except (ImportError, RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path}")
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    from ..connector_dev import scaffold_connector

    try:
        written = scaffold_connector(args.name, args.dir, label=args.label, description=args.description)
    except (ValueError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"wrote {path}")
    print("next: edit operations.py + info.json, then `pyfsr connector package` and `pyfsr connector deploy`")
    return 0


def cmd_package(args: argparse.Namespace) -> int:
    from ..connector_dev import package_connector

    try:
        tgz = package_connector(args.dir, out_dir=args.out_dir)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(tgz)
    return 0


def cmd_deploy(args: argparse.Namespace) -> int:
    from ..config import EnvConfig
    from ..connector_dev import deploy_connector

    try:
        cfg = EnvConfig.from_env_file(args.env_file) if args.env_file else EnvConfig.from_env()
        client = cfg.client()
    except Exception as exc:  # noqa: BLE001 - config problems reported, not raised
        print(f"error: cannot build a client from the environment: {exc}", file=sys.stderr)
        return 1
    config = None
    if args.config_json:
        try:
            config = json.loads(args.config_json)
        except ValueError as exc:
            print(f"error: --config-json is not valid JSON: {exc}", file=sys.stderr)
            return 1
    try:
        result = deploy_connector(
            client,
            args.source,
            config=config,
            config_name=args.config_name,
            replace=not args.no_replace,
            timeout=args.timeout,
        )
    except (FileNotFoundError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


def build_subparser(sub: argparse._SubParsersAction) -> None:
    """Wire the ``connector`` subcommands onto an existing subparsers object."""
    p_scaffold = sub.add_parser("scaffold", help="write a minimal installable connector skeleton")
    p_scaffold.add_argument("name", help="connector machine name (lowercase-hyphen, e.g. acme-threat-feed)")
    p_scaffold.add_argument("--dir", help="target directory (default: ./<name>)")
    p_scaffold.add_argument("--label", help="display label (default: the name title-cased)")
    p_scaffold.add_argument("--description", default="A custom FortiSOAR connector.", help="connector description")
    p_scaffold.set_defaults(func=cmd_scaffold)

    p_package = sub.add_parser("package", help="build dist/<name>-<version>.tgz from a connector checkout")
    p_package.add_argument("dir", nargs="?", default=".", help="connector checkout (default: current folder)")
    p_package.add_argument("--out-dir", help="where to write the bundle (default: <dir>/dist)")
    p_package.set_defaults(func=cmd_package)

    p_deploy = sub.add_parser("deploy", help="install a connector dir or .tgz on the FSR_* appliance")
    p_deploy.add_argument("source", help="connector checkout dir (packaged first) or a built .tgz")
    p_deploy.add_argument("--env-file", help="load FSR_* from this env file instead of the environment")
    p_deploy.add_argument("--config-json", help="JSON object of configuration field values to upsert as default")
    p_deploy.add_argument("--config-name", default="Default", help="configuration label (default 'Default')")
    p_deploy.add_argument("--no-replace", action="store_true", help="fail instead of replacing the same version")
    p_deploy.add_argument("--timeout", type=float, default=180.0, help="activation wait in seconds (default 180)")
    p_deploy.set_defaults(func=cmd_deploy)

    p_icon = sub.add_parser("icon", help="make a connector's 80x80 + 32x32 icon PNGs from an SVG")
    p_icon.add_argument("svg", help="the icon SVG (e.g. downloaded from icons.fortinet.com)")
    p_icon.add_argument(
        "--connector",
        default=".",
        help="the connector folder (holds info.json; PNGs go to its images/); default: the current folder",
    )
    p_icon.add_argument(
        "--wordmark-from",
        metavar="DIR",
        help="take the FORTINET wordmark from the icons in this folder (default: the connector's current icons)",
    )
    p_icon.add_argument("--no-wordmark", action="store_true", help="artwork only, no FORTINET wordmark")
    p_icon.set_defaults(func=cmd_icon)
