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

from . import _output


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


def _make_client(args):
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


def _compile(args):
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
def cmd_compile(args) -> int:
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


def cmd_validate(args) -> int:
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
    return 0 if result.ok else 1


def cmd_deploy(args) -> int:
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
    created = client.workflow_collections.import_export(result.fsr_json, replace=args.replace)
    rows = [[c.get("name", ""), c.get("uuid", "")] for c in created]
    _output.render(rows, ["created collection", "uuid"], fmt="table")
    return 0


def _workflows_of(result, collection_name: str) -> list[str]:
    for col in (result.fsr_json or {}).get("data", []):
        if col.get("name") == collection_name:
            return [w.get("name", "") for w in col.get("workflows", []) or []]
    return []


def build_subparser(asub) -> None:
    """Wire the ``playbook`` subcommands onto an existing subparsers object."""
    p_compile = asub.add_parser("compile", help="compile YAML to the FSR import envelope (offline)")
    add_connection_args(p_compile)  # harmless here; keeps args uniform
    p_compile.add_argument("file", help="playbook YAML file")
    p_compile.add_argument("-o", "--out", help="write envelope JSON to this file (else stdout)")
    p_compile.set_defaults(func=cmd_compile)

    p_validate = asub.add_parser("validate", help="compile and report diagnostics (offline)")
    p_validate.add_argument("file", help="playbook YAML file")
    p_validate.set_defaults(func=cmd_validate)

    p_deploy = asub.add_parser("deploy", help="compile YAML and create the playbook on the appliance")
    add_connection_args(p_deploy)
    p_deploy.add_argument("file", help="playbook YAML file")
    p_deploy.add_argument("--replace", action="store_true", help="hard-delete + recreate if it exists")
    p_deploy.add_argument("--dry-run", action="store_true", help="compile and list what would be created")
    p_deploy.set_defaults(func=cmd_deploy)
