"""``pyfsr connector`` command group -- local connector-package helpers (no appliance).

Subcommands:

- ``icon <svg> --connector DIR`` -- build the connector's large (80x80) and small (32x32)
  icon PNGs from an SVG (e.g. one from https://icons.fortinet.com), laid out like the
  stock Fortinet connector icons. See :mod:`pyfsr.connector_icon`.
"""

from __future__ import annotations

import argparse
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


def build_subparser(sub: argparse._SubParsersAction) -> None:
    """Wire the ``connector`` subcommands onto an existing subparsers object."""
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
