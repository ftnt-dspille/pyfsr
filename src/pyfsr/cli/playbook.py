"""``pyfsr playbook`` command group — author playbooks in YAML and deploy them.

Unlike the SSH-based ``appliance`` group, these subcommands talk to the
FortiSOAR **API**, so they build a :class:`~pyfsr.client.FortiSOAR` from the
``FSR_*`` environment (see :class:`~pyfsr.config.EnvConfig`) with optional CLI
overrides.

Subcommands:

- ``compile <file.yaml> [-o out.json]`` — compile only (no network); emit the
  ``workflow_collections`` envelope, diagnostics to stderr.
- ``validate <file.yaml>`` — compile and report diagnostics; nonzero exit on
  blocking errors.
- ``deploy <file.yaml> [--replace] [--dry-run]`` — compile then import via the
  API client.

The compiler is the optional ``pyfsr[playbooks]`` extra; handlers import it
lazily so the rest of the CLI works without it.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

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


def _compile(args: argparse.Namespace) -> CompiledPlaybook:
    """Compile the YAML file, printing diagnostics to stderr. Returns the result."""
    from ..authoring import compile_playbook_yaml, format_diagnostic

    text = _read(args.file)
    result = compile_playbook_yaml(text)
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
    result = _compile(args)
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
    client = _make_client(args)
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
    result = _compile(args)
    if not result.ok:
        print("error: compilation failed (see diagnostics above)", file=sys.stderr)
        return 1
    client = _make_client(args)
    findings = _connector_findings(client, result)
    _print_findings(findings)
    return 2 if findings else 0


def _workflows_of(result: CompiledPlaybook, collection_name: str) -> list[str]:
    for col in (result.fsr_json or {}).get("data", []):
        if col.get("name") == collection_name:
            return [w.get("name", "") for w in col.get("workflows", []) or []]
    return []


def build_subparser(asub: argparse._SubParsersAction) -> None:
    """Wire the ``playbook`` subcommands onto an existing subparsers object."""
    p_compile = asub.add_parser("compile", help="compile YAML to the FSR import envelope (offline)")
    add_connection_args(p_compile)  # harmless here; keeps args uniform
    p_compile.add_argument("file", help="playbook YAML file")
    p_compile.add_argument("-o", "--out", help="write envelope JSON to this file (else stdout)")
    p_compile.set_defaults(func=cmd_compile)

    p_validate = asub.add_parser("validate", help="compile and report diagnostics (offline)")
    add_connection_args(p_validate)  # only used with --check-connectors
    p_validate.add_argument("file", help="playbook YAML file")
    p_validate.add_argument(
        "--check-connectors",
        action="store_true",
        help="also warn about connector steps with no config on the target (needs a connection)",
    )
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
    p_deploy.set_defaults(func=cmd_deploy)

    p_lint = asub.add_parser(
        "lint",
        help="compile, then warn about connector steps with no config on the target (live preflight)",
    )
    add_connection_args(p_lint)
    p_lint.add_argument("file", help="playbook YAML file")
    p_lint.set_defaults(func=cmd_lint)

    p_fresh = asub.add_parser(
        "check-fresh",
        help="compare the cached compile catalog against a live SOAR (Level-1 probe)",
    )
    add_connection_args(p_fresh)
    p_fresh.add_argument("--db", help="reference catalog path (default: packaged/dev DB)")
    p_fresh.set_defaults(func=cmd_check_fresh)
