"""``pyfsr playbook`` command group — author playbooks in YAML and deploy them.

Unlike the SSH-based ``appliance`` group, these subcommands talk to the
FortiSOAR **API**, so they build a :class:`~pyfsr.client.FortiSOAR` from the
``FSR_*`` environment (see :class:`~pyfsr.config.EnvConfig`) with optional CLI
overrides.

Subcommands (this group is the authoring "start here" index):

- ``steps`` — list every friendly ``type:`` keyword with its canonical FSR
  name and one-line purpose (offline).
- ``step-help <type> [--schema]`` — keys + a real compiling friendly-YAML
  example for one step type (offline).
- ``examples [--intent ".."] [--stage S] [--manifest]`` -- list the foundational
  playbook library (whole, compiling, use-case-shaped worked examples an agent
  retrieves and adapts); ``--manifest`` emits the retrieval JSON payload (offline).
- ``show <slug>`` — print one library playbook's metadata + full friendly YAML (offline).
- ``compile <file.yaml> [-o out.json]`` — compile only (no network); emit the
  ``workflow_collections`` envelope, diagnostics to stderr.
- ``validate <file.yaml>`` — compile and report diagnostics; nonzero exit on
  blocking errors.
- ``lint <file.yaml>`` — live preflight: warn about connector steps with no
  config on the target.
- ``deploy <file.yaml> [--replace] [--dry-run]`` — compile then import via the
  API client.
- ``check-fresh`` — compare the cached compile catalog against a live SOAR.

``compile``/``validate``/``deploy``/``lint`` accept ``--refresh-catalog``: warm
the reference catalog from the live instance before compiling so connector and
operation tokens (including custom connectors like ``code-runner``) resolve to
real labels/versions. Without it the offline slim catalog carries no connector
rows, so connector steps compile without a ``name``/``version`` and the playbook
editor renders them as "undefined".

Runtime helper (Python SDK, not a CLI command): ``client.manual_input.answer()``
drives a paused Manual Input / Approval step in one call.

The compiler is the optional ``pyfsr[playbooks]`` extra; handlers import it
lazily so the rest of the CLI works without it.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any, cast

from . import _output

if TYPE_CHECKING:
    import sqlite3

    from ..authoring import CompiledPlaybook
    from ..client import FortiSOAR


def add_connection_args(p: argparse.ArgumentParser) -> None:
    """API-connection overrides; anything omitted falls back to ``FSR_*`` env."""
    g = p.add_argument_group("connection (overrides FSR_* env)")
    g.add_argument("--server", help="appliance host or URL (FSR_BASE_URL)")
    g.add_argument("--token", "--api-key", dest="token", help="API key (FSR_API_KEY)")
    g.add_argument("--username", help="login user (FSR_USERNAME)")
    g.add_argument("--password", help="login password (FSR_PASSWORD)")
    g.add_argument("--port", type=int, help="port override (FSR_PORT)")
    g.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="disable TLS verification (lab boxes with self-signed certs)",
    )


def _make_client(args: argparse.Namespace) -> FortiSOAR:
    """Build a FortiSOAR client from FSR_* env plus CLI overrides."""
    from ..config import EnvConfig

    overrides: dict = {}
    if getattr(args, "server", None):
        overrides["base_url"] = args.server
    if getattr(args, "token", None):
        overrides["auth"] = args.token
    elif getattr(args, "username", None) and getattr(args, "password", None):
        overrides["auth"] = (args.username, args.password)
    if getattr(args, "port", None) is not None:
        overrides["port"] = args.port
    if getattr(args, "no_verify_ssl", False):
        overrides["verify_ssl"] = False
        overrides["suppress_insecure_warnings"] = True

    # When a full connection is supplied via flags, don't require FSR_* env.
    if "base_url" in overrides and "auth" in overrides:
        from ..client import FortiSOAR

        return FortiSOAR(**overrides)
    return EnvConfig.from_env().client(**overrides)


def add_refresh_catalog_arg(p: argparse.ArgumentParser) -> None:
    """Opt-in: warm the reference catalog live before compiling."""
    p.add_argument(
        "--refresh-catalog",
        action="store_true",
        help=(
            "warm the reference catalog from the live instance before compiling "
            "(needs a connection). Resolves connector/operation tokens — including "
            "custom connectors like code-runner — to real labels/versions so the "
            "playbook editor doesn't show 'undefined' for connector steps."
        ),
    )


def _compile(args: argparse.Namespace, *, client: FortiSOAR | None = None) -> CompiledPlaybook:
    """Compile the YAML file, printing diagnostics to stderr. Returns the result.

    When ``--refresh-catalog`` is set (or a ``client`` is supplied by the caller),
    the per-user reference catalog is warmed from the live instance first, so
    connector/operation tokens resolve to real labels/versions. Without it the
    compile is offline against the packaged slim catalog, which carries no
    connector rows — connector steps then compile without a ``name``/``version``/
    ``operationTitle`` and the editor canvas renders them as "undefined".
    """
    from ..authoring import compile_playbook_yaml, format_diagnostic

    text = _read(args.file)
    if client is None and getattr(args, "refresh_catalog", False):
        client = _make_client(args)
    result = compile_playbook_yaml(text, client=client)
    for diag in result.errors:
        print(format_diagnostic(diag), file=sys.stderr)
    return result


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# --- handlers ------------------------------------------------------------
def cmd_compile(args: argparse.Namespace) -> int:
    result = _compile(args)
    if not result.ok:
        print("error: compilation failed (see diagnostics above)", file=sys.stderr)
        return 1
    payload = json.dumps(result.fsr_json, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(payload)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    result = _compile(args)
    counts = {"collections": len(result.collection_names), "playbooks": len(result.playbook_names)}
    summary = "OK" if result.ok else "FAILED"
    _output.kv(
        {
            "result": summary,
            "collections": counts["collections"],
            "playbooks": counts["playbooks"],
            "errors": len(result.blocking),
            "warnings": len(result.warnings),
        },
        fmt="table",
        file=sys.stderr,
    )
    # Opt-in live preflight: connector steps with no config on the target.
    # Warnings never flip the validate exit code (they don't break compilation).
    if result.ok and getattr(args, "check_connectors", False):
        _print_findings(_connector_findings(_make_client(args), result))
    return 0 if result.ok else 1


def cmd_deploy(args: argparse.Namespace) -> int:
    # A non-dry-run deploy always needs a client to post; build it once up front
    # so --refresh-catalog warms the catalog through the same connection instead
    # of opening a second one.
    client = _make_client(args) if (args.refresh_catalog or not args.dry_run) else None
    result = _compile(args, client=client)
    if not result.ok:
        print("error: compilation failed (see diagnostics above)", file=sys.stderr)
        return 1
    if args.dry_run:
        print("# dry-run — nothing posted", file=sys.stderr)
        _output.render(
            [[c, ", ".join(_workflows_of(result, c))] for c in result.collection_names],
            ["collection", "playbooks"],
            fmt="table",
        )
        return 0
    assert client is not None  # built above for any non-dry-run deploy
    # result.ok was checked above, so fsr_json is populated.
    assert result.fsr_json is not None
    # Opt-in live preflight before posting; warn-only, never aborts the deploy.
    if getattr(args, "check_connectors", False):
        _print_findings(_connector_findings(client, result))
    created: list[dict[str, Any]] = client.workflow_collections.import_export(result.fsr_json, replace=args.replace)
    rows = [[c.get("name", ""), c.get("uuid", "")] for c in created]
    _output.render(rows, ["created collection", "uuid"], fmt="table")
    return 0


def _catalog_conn(args: argparse.Namespace) -> tuple[sqlite3.Connection, str]:
    """Open the fsr_playbooks reference catalog (read/write) for freshness ops.

    Honors ``--db`` then the package's own resolution (``$FSRPB_DB`` → dev DB →
    packaged slim DB). Raises the same clear error as the compiler when the
    optional ``fsr_playbooks`` extra is absent."""
    import sqlite3

    from ..authoring import _load_compiler  # reuses the missing-dep message

    _, default_db_path = _load_compiler()
    db = getattr(args, "db", None) or str(default_db_path())
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn, db


def cmd_check_fresh(args: argparse.Namespace) -> int:
    """Level-1 freshness probe: compare the cached catalog's provenance against
    a live SOAR. Exit 0 = fresh, 2 = drift detected, 1 = error / unstamped."""
    from fsr_playbooks import _catalog_meta

    from ..playbook_freshness import compare, probe_live

    conn, db = _catalog_conn(args)
    try:
        stored = _catalog_meta.get_all(conn)
    finally:
        conn.close()
    if not stored.get("base_url_hash"):
        print(
            f"catalog {db} carries no provenance stamp — run `warmup` against a target SOAR first.",
            file=sys.stderr,
        )
        return 1

    client = _make_client(args)
    live = probe_live(client)
    report = compare(stored, live)

    _output.kv(
        {
            "catalog": db,
            "instance": report.instance_label or "(unlabeled)",
            "fsr_version": f"{stored.get('fsr_version')} -> {live.get('version')}",
            "result": "FRESH" if report.is_fresh else "STALE",
        },
        fmt="table",
        file=sys.stderr,
    )
    if report.drift:
        print("drift detected:", file=sys.stderr)
        for line in report.drift:
            print(f"  - {line}", file=sys.stderr)
        print(
            "re-run `warmup` against the target to refresh the catalog.",
            file=sys.stderr,
        )
        return 2
    print("catalog is up to date with the live instance.", file=sys.stderr)
    return 0


def _connector_findings(client: FortiSOAR, result: CompiledPlaybook) -> list:
    """Run the live-target connector-config preflight on a compiled playbook.

    Returns the list of :class:`~pyfsr.playbook_lint.LintFinding` (empty when
    clean). Shared by ``lint`` and the opt-in ``--check-connectors`` on
    deploy/validate."""
    from ..playbook_lint import check_connector_configs, connector_refs

    assert result.fsr_json is not None
    refs = connector_refs(result.fsr_json)
    return check_connector_configs(client, refs)


def _print_findings(findings: list) -> None:
    """Render lint findings as a table on stderr (no-op message when clean)."""
    if not findings:
        print("connector preflight: OK — every connector step is configured.", file=sys.stderr)
        return
    print(f"connector preflight: {len(findings)} warning(s)", file=sys.stderr)
    _output.render(
        [[f.connector, f.code, f.message, f.fix_hint] for f in findings],
        ["connector", "issue", "detail", "fix"],
        fmt="table",
        file=sys.stderr,
    )


def cmd_lint(args: argparse.Namespace) -> int:
    """Compile, then warn about connector steps with no config on the target.

    Exit 0 = clean, 2 = warnings, 1 = compile/connection error — mirrors
    ``check-fresh``. Never blocks: a playbook may be deployed before its
    connector configs are created."""
    # _compile() warms the catalog itself when --refresh-catalog is set; the
    # preflight client is built only after a clean compile (so a compile failure
    # exits 1 without needing a connection).
    result = _compile(args)
    if not result.ok:
        print("error: compilation failed (see diagnostics above)", file=sys.stderr)
        return 1
    client = _make_client(args)
    findings = _connector_findings(client, result)
    _print_findings(findings)
    return 2 if findings else 0


def cmd_steps(args: argparse.Namespace) -> int:
    """List every friendly ``type:`` keyword with its canonical name + purpose."""
    from ..playbook_catalog import list_step_types

    infos = list_step_types()
    rows = [[i.short, i.canonical, "yes" if i.modeled else "", i.purpose] for i in infos]
    _output.render(rows, ["type", "fsr step type", "typed", "purpose"], fmt="table")
    print(
        f"\n{len(infos)} step types. Run `pyfsr playbook step-help <type>` for keys + a compiling example.",
        file=sys.stderr,
    )
    return 0


def cmd_step_help(args: argparse.Namespace) -> int:
    """Show authoring help + a real compiling example for one step type."""
    from ..playbook_catalog import step_help

    try:
        h = step_help(args.type)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _output.kv(
        {
            "type": h.short,
            "fsr step type": h.canonical,
            "label": h.label,
            "purpose": h.purpose,
            "offline-validated": "yes (typed-args schema)" if h.modeled else "no",
        },
        fmt="table",
    )
    if h.pitfalls:
        print(f"\ncommon pitfalls:\n  {h.pitfalls}")
    if args.schema and h.arg_schema:
        print("\narguments JSON schema:")
        print(json.dumps(h.arg_schema, indent=2))
    if h.example_yaml:
        print("\nexample (friendly YAML excerpt from a real playbook):\n")
        print(h.example_yaml)
    else:
        print(
            "\n(no bundled example for this type yet -- see guides/playbook-yaml-reference.md)",
            file=sys.stderr,
        )
    return 0


def cmd_examples(args: argparse.Namespace) -> int:
    """List the foundational playbook library (the worked-examples layer).

    Prints every library playbook with its stage, intent (goal), step types, and
    compile status — the table an agent scans to find the closest worked example to
    adapt. With ``--intent`` filters by goal substring; ``--manifest`` emits the
    retrieval JSON payload instead of a table; ``--stage`` filters by stage.
    """
    from ..playbook_library import library_manifest, list_library

    if args.manifest:
        print(json.dumps(library_manifest(), indent=2))
        return 0

    entries = list_library()
    if not entries:
        print(
            "no library found — examples/playbooks/library/ is the worked-examples layer.",
            file=sys.stderr,
        )
        return 1
    if getattr(args, "stage", None):
        entries = [e for e in entries if e.stage == args.stage]
    if getattr(args, "intent", None):
        needle = args.intent.lower()
        entries = [e for e in entries if needle in e.goal.lower() or needle in e.name.lower()]

    rows = [
        [
            e.stage,
            e.slug,
            e.goal[:60] + ("…" if len(e.goal) > 60 else ""),
            ",".join(e.step_types),
            "yes" if e.compiles_ok else "cold*",
        ]
        for e in entries
    ]
    _output.render(rows, ["stage", "slug", "goal", "step types", "compiles"], fmt="table")
    print(
        f"\n{len(entries)} playbooks. cold* = still fails to compile against the fixture "
        f"connector catalog -- a real content bug in the example, not just a missing "
        f"connector. Run `pyfsr playbook show <slug>` for one in full.",
        file=sys.stderr,
    )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    """Print one library playbook: its metadata + the full friendly YAML."""
    from ..playbook_library import library_show

    entry = library_show(args.slug)
    if entry is None:
        print(f"error: no library playbook with slug {args.slug!r}", file=sys.stderr)
        print(
            "run `pyfsr playbook examples` to list available slugs.",
            file=sys.stderr,
        )
        return 1
    _output.kv(
        {
            "slug": entry.slug,
            "stage": entry.stage,
            "name": entry.name,
            "goal": entry.goal,
            "source": entry.source,
            "path": entry.path,
            "step_types": ",".join(entry.step_types),
            "connectors": ",".join(entry.connectors) or "(none)",
            "jinja_filters": ",".join(entry.jinja_filters) or "(none)",
            "triggers": ",".join(entry.triggers) or "(none)",
            "compiles_ok": "yes" if entry.compiles_ok else "cold*",
        },
        fmt="table",
    )
    print("\nfriendly YAML:\n")

    from ..playbook_library import _LIBRARY_DEFAULT

    # entry.path is repo-relative; resolve against the library dir's repo root,
    # which is two parents above the library directory (examples/playbooks/library).
    repo_root = _LIBRARY_DEFAULT.parents[2]
    print((repo_root / entry.path).read_text(encoding="utf-8"))
    return 0


def _workflows_of(result: CompiledPlaybook, collection_name: str) -> list[str]:
    for col in (result.fsr_json or {}).get("data", []):
        if col.get("name") == collection_name:
            return [w.get("name", "") for w in col.get("workflows", []) or []]
    return []


# --- version-control (snapshots) handlers ---------------------------------
def _versions_format(args: argparse.Namespace) -> str:
    return "json" if getattr(args, "json", False) else "table"


def _version_row(v: Any) -> list[str]:
    """One table row for a PlaybookVersion (newest-first caller sorts already)."""
    wf = v.workflow
    wf_name = wf.get("name") if isinstance(wf, dict) else ""
    return [
        v.uuid or "",
        v.note or "",
        "autosave" if v.autosave else "snapshot",
        _ts(v.modify_date),
        wf_name or "",
    ]


def _ts(epoch: float | None) -> str:
    """Render an epoch-second modifyDate as a local timestamp (blank if None)."""
    import datetime as _dt

    if not epoch:
        return ""
    try:
        return _dt.datetime.fromtimestamp(float(epoch)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(epoch)


def cmd_versions_list(args: argparse.Namespace) -> int:
    client = _make_client(args)
    vers = cast(list, client.playbooks.list_versions(args.playbook, limit=args.limit))
    vers.sort(key=lambda v: v.modify_date or 0, reverse=True)
    if getattr(args, "json", False):
        # list-of-objects (model_dump preserves the wire ``json`` alias etc.).
        import json as _json

        _json.dump([v.model_dump(by_alias=True) for v in vers], sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0
    rows = [_version_row(v) for v in vers]
    _output.render(rows, ["uuid", "note", "type", "modified", "playbook"], fmt="table")
    return 0


def cmd_versions_get(args: argparse.Namespace) -> int:
    client = _make_client(args)
    v = client.playbooks.get_version(args.version)
    if getattr(args, "json", False):
        _output.kv(v.model_dump(by_alias=True), fmt="json")
        return 0
    _output.kv(
        {
            "uuid": v.uuid,
            "note": v.note,
            "type": "autosave" if v.autosave else "snapshot",
            "modified": _ts(v.modify_date),
            "playbook": (v.workflow.get("name") if isinstance(v.workflow, dict) else v.workflow_iri),
        },
        fmt="table",
    )
    if getattr(args, "show_json", False) and v.snapshot:
        print(v.snapshot)
    return 0


def cmd_versions_create(args: argparse.Namespace) -> int:
    client = _make_client(args)
    v = client.playbooks.create_version(args.playbook, note=args.note or "")
    print(f"created snapshot {v.uuid} (note={v.note!r}) on {args.playbook}", file=sys.stderr)
    if getattr(args, "json", False):
        _output.kv(v.model_dump(by_alias=True), fmt="json")
    return 0


def _confirm(args: argparse.Namespace, verb: str, what: str) -> bool:
    if getattr(args, "yes", False):
        return True
    print(f"About to {verb} {what}. Continue? [y/N] ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def cmd_versions_restore(args: argparse.Namespace) -> int:
    what = f"overwrite playbook {args.playbook!r} with version {args.version}"
    if not _confirm(args, "restore (replace live playbook with snapshot)", what):
        print("aborted", file=sys.stderr)
        return 1
    client = _make_client(args)
    wf = client.playbooks.restore_version(args.playbook, args.version)
    print(
        f"restored {args.playbook!r} from version {args.version} -> {wf.name}",
        file=sys.stderr,
    )
    if getattr(args, "json", False):
        _output.kv(wf.to_dict(by_alias=True) if hasattr(wf, "to_dict") else dict(wf), fmt="json")
    return 0


def cmd_versions_delete(args: argparse.Namespace) -> int:
    if not _confirm(args, "delete version", args.version):
        print("aborted", file=sys.stderr)
        return 1
    client = _make_client(args)
    client.playbooks.delete_version(args.version)
    print(f"deleted version {args.version}", file=sys.stderr)
    return 0


def cmd_versions_diff(args: argparse.Namespace) -> int:
    client = _make_client(args)
    d = client.playbooks.diff_versions(args.a, args.b)
    if getattr(args, "json", False):
        _output.kv(d.model_dump(by_alias=True), fmt="json")
        return 0
    _output.render(
        [[s, c.field] for c in d.changed for s in (c.step,)],
        ["step", "field"],
        fmt="table",
    )
    if d.added:
        print(f"added steps:    {', '.join(d.added)}")
    if d.removed:
        print(f"removed steps:  {', '.join(d.removed)}")
    if d.routes_added:
        print(f"routes added:   {', '.join(d.routes_added)}")
    if d.routes_removed:
        print(f"routes removed: {', '.join(d.routes_removed)}")
    if not d.changed and not d.added and not d.removed and not d.routes_added and not d.routes_removed:
        print("no differences (identical snapshots)")
    return 0


def build_subparser(asub: argparse._SubParsersAction) -> None:
    """Wire the ``playbook`` subcommands onto an existing subparsers object."""
    p_steps = asub.add_parser("steps", help="list every friendly step type with its purpose (offline)")
    p_steps.set_defaults(func=cmd_steps)

    p_step_help = asub.add_parser(
        "step-help",
        help="show keys + a real compiling example for one step type (offline)",
    )
    p_step_help.add_argument("type", help="friendly type (set_variable) or FSR name (SetVariable)")
    p_step_help.add_argument(
        "--schema", action="store_true", help="also print the arguments JSON schema (modeled types)"
    )
    p_step_help.set_defaults(func=cmd_step_help)

    p_examples = asub.add_parser(
        "examples",
        help="list the foundational playbook library (worked examples to adapt) (offline)",
    )
    p_examples.add_argument("--intent", help="filter by goal/name substring (case-insensitive)")
    p_examples.add_argument("--stage", help="filter by stage (triggers/enrichment/decision/action/notify/control)")
    p_examples.add_argument(
        "--manifest", action="store_true", help="emit the retrieval manifest JSON instead of a table"
    )
    p_examples.set_defaults(func=cmd_examples)

    p_show = asub.add_parser("show", help="print one library playbook: metadata + the full friendly YAML (offline)")
    p_show.add_argument("slug", help="library playbook slug (see `pyfsr playbook examples`)")
    p_show.set_defaults(func=cmd_show)

    p_compile = asub.add_parser("compile", help="compile YAML to the FSR import envelope (offline)")
    add_connection_args(p_compile)  # harmless here; keeps args uniform
    p_compile.add_argument("file", help="playbook YAML file")
    p_compile.add_argument("-o", "--out", help="write envelope JSON to this file (else stdout)")
    add_refresh_catalog_arg(p_compile)
    p_compile.set_defaults(func=cmd_compile)

    p_validate = asub.add_parser("validate", help="compile and report diagnostics (offline)")
    add_connection_args(p_validate)  # only used with --check-connectors
    p_validate.add_argument("file", help="playbook YAML file")
    p_validate.add_argument(
        "--check-connectors",
        action="store_true",
        help="also warn about connector steps with no config on the target (needs a connection)",
    )
    add_refresh_catalog_arg(p_validate)
    p_validate.set_defaults(func=cmd_validate)

    p_deploy = asub.add_parser("deploy", help="compile YAML and create the playbook on the appliance")
    add_connection_args(p_deploy)
    p_deploy.add_argument("file", help="playbook YAML file")
    p_deploy.add_argument("--replace", action="store_true", help="hard-delete + recreate if it exists")
    p_deploy.add_argument("--dry-run", action="store_true", help="compile and list what would be created")
    p_deploy.add_argument(
        "--check-connectors",
        action="store_true",
        help="warn about connector steps with no config on the target before posting",
    )
    add_refresh_catalog_arg(p_deploy)
    p_deploy.set_defaults(func=cmd_deploy)

    p_lint = asub.add_parser(
        "lint",
        help="compile, then warn about connector steps with no config on the target (live preflight)",
    )
    add_connection_args(p_lint)
    p_lint.add_argument("file", help="playbook YAML file")
    add_refresh_catalog_arg(p_lint)
    p_lint.set_defaults(func=cmd_lint)

    p_fresh = asub.add_parser(
        "check-fresh",
        help="compare the cached compile catalog against a live SOAR (Level-1 probe)",
    )
    add_connection_args(p_fresh)
    p_fresh.add_argument("--db", help="reference catalog path (default: packaged/dev DB)")
    p_fresh.set_defaults(func=cmd_check_fresh)

    # --- versions: saved-snapshot history (the editor's "Versions" tab) ---
    p_ver = asub.add_parser(
        "versions",
        help="list/get/create/restore/delete/diff a playbook's saved snapshots",
        description=(
            "Playbook version control: saved snapshots (the 'workflow_versions' "
            "module, i.e. the editor's 'Versions' tab). A snapshot freezes a "
            "playbook definition at a point in time; restore overwrites the live "
            "playbook with a snapshot's content.\n\n"
            "Subcommands: list / get / create / restore / delete / diff"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    vsub = p_ver.add_subparsers(dest="versions_command", required=True)

    p_vl = vsub.add_parser("list", help="list a playbook's saved snapshots (newest first)")
    add_connection_args(p_vl)
    p_vl.add_argument("playbook", help="playbook uuid, IRI, or name")
    p_vl.add_argument("--limit", type=int, default=100, help="max snapshots (default 100)")
    p_vl.add_argument("--json", action="store_true", help="emit raw JSON instead of a table")
    p_vl.set_defaults(func=cmd_versions_list)

    p_vg = vsub.add_parser("get", help="show one saved snapshot")
    add_connection_args(p_vg)
    p_vg.add_argument("version", help="version uuid or /api/3/workflow_versions/<uuid> IRI")
    p_vg.add_argument("--json", action="store_true", help="emit the full record as JSON")
    p_vg.add_argument(
        "--show-json",
        action="store_true",
        help="also print the snapshot's stringified workflow payload (the 'json' field)",
    )
    p_vg.set_defaults(func=cmd_versions_get)

    p_vc = vsub.add_parser("create", help="save a snapshot of the playbook as it is now")
    add_connection_args(p_vc)
    p_vc.add_argument("playbook", help="playbook uuid, IRI, or name")
    p_vc.add_argument("--note", default="", help="label for the snapshot")
    p_vc.add_argument("--json", action="store_true", help="emit the created record as JSON")
    p_vc.set_defaults(func=cmd_versions_create)

    p_vr = vsub.add_parser(
        "restore",
        help="overwrite the live playbook with a snapshot's content (destructive)",
    )
    add_connection_args(p_vr)
    p_vr.add_argument("playbook", help="playbook uuid, IRI, or name")
    p_vr.add_argument("version", help="version uuid or IRI to restore from")
    p_vr.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p_vr.add_argument("--json", action="store_true", help="emit the restored workflow as JSON")
    p_vr.set_defaults(func=cmd_versions_restore)

    p_vd = vsub.add_parser("delete", help="delete a saved snapshot (the playbook itself is untouched)")
    add_connection_args(p_vd)
    p_vd.add_argument("version", help="version uuid or IRI")
    p_vd.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p_vd.set_defaults(func=cmd_versions_delete)

    p_vdiff = vsub.add_parser("diff", help="compare two snapshots' step graphs (client-side)")
    add_connection_args(p_vdiff)
    p_vdiff.add_argument("a", help="first version uuid/IRI")
    p_vdiff.add_argument("b", help="second version uuid/IRI")
    p_vdiff.add_argument("--json", action="store_true", help="emit the diff as JSON")
    p_vdiff.set_defaults(func=cmd_versions_diff)
