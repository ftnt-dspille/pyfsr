"""``pyfsr repo`` command group ��� discover and download from Fortinet's content repo.

Unlike the API-based ``playbook``/``records`` groups (which need a live
appliance), these subcommands hit Fortinet's **public, unauthenticated** content
repository (``repo.fortisoar.fortinet.com``) — no box, no token. They wrap
:mod:`pyfsr.repo`.

Subcommands:

- ``reachable`` — cheap reachability check (exit 0/1).
- ``list-connectors [--category CAT]`` — every connector in the public manifest
  (latest version only; ~720 rows).
- ``search <term>`` — case-insensitive substring search over the manifest.
- ``versions <name>`` — every published version of one connector.
- ``info <kind> <name> <version>`` — per-version ``info.json`` for a
  connector / widget / solution-pack.
- ``download <kind> <name> <version> [--dest PATH]`` — fetch a specific-version
  archive.

Discovery note: connectors have a public manifest, so ``list-connectors`` /
``search`` / ``versions`` are full no-appliance discovery. Widgets and
solution-packs have **no public manifest** — use ``client.content_hub`` on an
appliance to list/search those, then ``info``/``download`` here once you know
the slug+version.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from ..exceptions import RepoArtifactNotFoundError, RepoUnreachableError
from . import _output

if TYPE_CHECKING:
    from ..models import ConnectorVersionInfo, SolutionPackInfo, WidgetInfo

# ``download`` dispatch table — kind name -> repo.download_* function.
_DOWNLOAD_KINDS = ("connector", "widget", "solution-pack")
# ``info`` dispatch table ��� kind name -> (repo.<fn>, model label).
_INFO_KINDS = ("connector", "widget", "solution-pack")


def _fmt_error(exc: Exception) -> int:
    """Print a repo exception to stderr and return the nonzero exit code."""
    print(f"error: {exc}", file=sys.stderr)
    return 1


def cmd_reachable(args: argparse.Namespace) -> int:
    """Cheap reachability check; exit 0 if the repo answers, 1 otherwise."""
    from .. import repo

    ok = repo.reachable(timeout=args.timeout)
    print("reachable" if ok else "unreachable")
    return 0 if ok else 1


def cmd_list_connectors(args: argparse.Namespace) -> int:
    """List every connector in the public manifest (latest version only)."""
    from .. import repo

    try:
        entries = repo.list_connectors()
    except (RepoUnreachableError, RepoArtifactNotFoundError) as exc:
        return _fmt_error(exc)

    if args.category:
        want = args.category.lower()
        entries = [e for e in entries if want in (e.category_str or "").lower()]
    rows = [[e.name, e.label, e.version, e.category_str or ""] for e in entries]
    # Pass ``file=sys.stdout`` dynamically so output respects redirection (and
    # test capture); ``_output`` binds its default at import time, which would
    # otherwise ignore a swapped ``sys.stdout``.
    _output.render(rows, ["name", "label", "version", "category"], fmt=args.fmt, file=sys.stdout)
    print(f"\n{len(entries)} connectors.", file=sys.stderr)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Search the connector manifest by free-text term (name/label/desc/category)."""
    from .. import repo

    try:
        entries = repo.search_connectors(args.term)
    except (RepoUnreachableError, RepoArtifactNotFoundError) as exc:
        return _fmt_error(exc)
    rows = [[e.name, e.label, e.version, e.category_str or ""] for e in entries]
    _output.render(rows, ["name", "label", "version", "category"], fmt=args.fmt, file=sys.stdout)
    print(f"\n{len(entries)} matches for {args.term!r}.", file=sys.stderr)
    return 0


def cmd_versions(args: argparse.Namespace) -> int:
    """Print every published version of one connector."""
    from .. import repo

    try:
        versions = repo.connector_versions(args.name)
    except (RepoUnreachableError, RepoArtifactNotFoundError) as exc:
        return _fmt_error(exc)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.fmt == "json":
        import json

        json.dump(versions, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        rows = [[v] for v in versions]
        _output.render(rows, ["version"], fmt=args.fmt, file=sys.stdout)
    print(f"\n{len(versions)} versions for {args.name!r}.", file=sys.stderr)
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    """Print the per-version info.json for a connector / widget / solution-pack."""
    from .. import repo

    # All three return ApiResult subclasses with different curated fields; type
    # as the union so the dispatch reads cleanly (only ``to_dict`` is used below).
    info: ConnectorVersionInfo | WidgetInfo | SolutionPackInfo
    try:
        if args.kind == "connector":
            info = repo.connector_info(args.name, args.version)
        elif args.kind == "widget":
            info = repo.widget_info(args.name, args.version)
        else:  # solution-pack
            info = repo.solution_pack_info(args.name, args.version)
    except (RepoUnreachableError, RepoArtifactNotFoundError) as exc:
        return _fmt_error(exc)

    # Flatten the typed model to a readable key/value card, with the more useful
    # fields ordered first. ``to_dict`` keeps extras (operations, contents, ...).
    data = info.to_dict(exclude_none=True)
    preferred = [
        "name",
        "label",
        "title",
        "version",
        "availableVersions",
        "compatibility",
        "fsrMinCompatibility",
        "category",
        "publisher",
        "certified",
        "description",
        "releaseNotes",
    ]
    ordered: dict = {}
    for k in preferred:
        if k in data:
            ordered[k] = data.pop(k)
    ordered.update(data)
    _output.kv(ordered, fmt=args.fmt, file=sys.stdout)
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    """Download a connector / widget / solution-pack archive by exact name+version."""
    from .. import repo

    fn = {
        "connector": repo.download_connector,
        "widget": repo.download_widget,
        "solution-pack": repo.download_solution_pack,
    }[args.kind]
    try:
        path = fn(args.name, args.version, args.dest)
    except (RepoUnreachableError, RepoArtifactNotFoundError) as exc:
        return _fmt_error(exc)
    print(path)
    return 0


def build_subparser(sub: argparse._SubParsersAction) -> None:
    """Wire the ``repo`` subcommands onto an existing subparsers object."""
    # Deferred so we reuse ``__main__``'s canonical ``--json``/``--csv`` helper
    # without a circular module-load (this runs at CLI parse time, after
    # ``__main__`` is fully initialized).
    from .__main__ import _add_fmt

    p_reach = sub.add_parser("reachable", help="cheap reachability check (exit 0/1)")
    p_reach.add_argument("--timeout", type=float, default=5.0, help="connect timeout (s)")
    p_reach.set_defaults(func=cmd_reachable)

    p_list = sub.add_parser(
        "list-connectors",
        help="list every connector in the public manifest (latest version only)",
    )
    p_list.add_argument("--category", help="filter by category substring (case-insensitive)")
    _add_fmt(p_list)
    p_list.set_defaults(func=cmd_list_connectors)

    p_search = sub.add_parser("search", help="search the connector manifest by free-text term")
    p_search.add_argument("term", help="search term (matches name/label/description/category)")
    _add_fmt(p_search)
    p_search.set_defaults(func=cmd_search)

    p_versions = sub.add_parser("versions", help="list every published version of a connector")
    p_versions.add_argument("name", help="connector slug (e.g. servicenow)")
    _add_fmt(p_versions)
    p_versions.set_defaults(func=cmd_versions)

    p_info = sub.add_parser(
        "info",
        help="per-version info.json for an artifact (see --help for kinds)",
    )
    p_info.add_argument("kind", choices=_INFO_KINDS, help="artifact kind")
    p_info.add_argument("name", help="artifact slug (e.g. servicenow, accessControl, fortindrEssentials)")
    p_info.add_argument("version", help="exact version (e.g. 1.0.0)")
    _add_fmt(p_info)
    p_info.set_defaults(func=cmd_info)

    p_dl = sub.add_parser(
        "download",
        help="download an artifact archive by exact name+version",
    )
    p_dl.add_argument("kind", choices=_DOWNLOAD_KINDS, help="artifact kind")
    p_dl.add_argument("name", help="artifact slug")
    p_dl.add_argument("version", help="exact version")
    p_dl.add_argument("--dest", help="target file or directory (default: cwd)")
    p_dl.set_defaults(func=cmd_download)
