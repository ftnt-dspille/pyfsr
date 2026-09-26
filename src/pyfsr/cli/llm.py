"""``pyfsr llm`` -- which LLM FortiAI uses, and swapping it for a third-party one.

::

    pyfsr llm status --instance lab
    OPENAI_API_KEY=... pyfsr llm setup --instance lab --profile "OpenAI GPT-4.1" --model GPT-4.1
    pyfsr llm assign --instance lab "OpenAI GPT-4.1"          # every agent; saves a snapshot
    pyfsr llm restore --instance lab <snapshot.json>          # back to what it was

The API key is read from an environment variable (``--api-key-env``, default
``OPENAI_API_KEY``) or a file (``--api-key-file``), never from the command line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .ai_eval import _client

SNAPSHOT_DIR = Path.home() / ".pyfsr" / "llm-snapshots"


def cmd_status(args: argparse.Namespace) -> int:
    client = _client(args)
    profiles = {c.uuid: c for c in client.ai.list_llm_configs()}
    print("reasoning profiles:")
    for c in profiles.values():
        print(
            f"  {'*' if c.isdefault else ' '} {c.name:24} provider={c.provider} model={c.modelname} "
            f"connector={c.config.get('connector_name')}"
        )
    print("providers offered in the wizard:", ", ".join(p.name or "?" for p in client.ai.list_providers()))
    print("agents:")
    for name, a in sorted(client.ai.llm_assignments().items()):
        record = profiles.get(a["llmconfig"])
        config = profiles.get(a["llm_provider"])
        print(
            f"  {name:28} config={config.name if config else a['llm_provider'] or '(default)'}"
            f"{'' if record is None else f'  record={record.name}'}"
        )
    try:
        bal = client.ai.token_balance()
        print(f"FortiAI tokens: {bal.remaining:,} of {bal.entitled_tokens + bal.entitled_topup_tokens:,} left")
    except Exception as exc:  # noqa: BLE001 - informational only
        print(f"FortiAI tokens: n/a ({exc})")
    return 0


def _api_key(args: argparse.Namespace) -> str:
    if args.api_key_file:
        key = Path(args.api_key_file).expanduser().read_text(encoding="utf-8").strip()
    else:
        key = os.environ.get(args.api_key_env, "").strip()
    if not key:
        source = args.api_key_file or f"${args.api_key_env}"
        raise SystemExit(f"no API key in {source}")
    return key


def cmd_setup(args: argparse.Namespace) -> int:
    from ..api.ai import LLMSetupError

    client = _client(args)
    extra: dict[str, Any] = {}
    if args.azure_endpoint:
        extra = {
            "api_type": True,
            "api_base": args.azure_endpoint,
            "api_version": args.azure_api_version,
            "deployment_id": args.azure_deployment,
        }
    try:
        profile = client.ai.setup_connector_llm(
            args.profile,
            connector=args.connector,
            model=args.model,
            api_key=_api_key(args),
            config_name=args.config_name,
            extra_config=extra,
            install=not args.no_install,
            verify=not args.no_verify,
        )
    except LLMSetupError as exc:
        print(f"setup failed: {exc}", file=sys.stderr)
        return 2
    print(f"profile {profile.name!r} ({profile.uuid}) -> {args.connector}/{args.model}")
    if args.assign:
        return _assign(client, args.profile, None, args)
    print(f"switch agents with: pyfsr llm assign {args.profile!r}")
    return 0


def _assign(client: Any, profile: str, agents: list[str] | None, args: argparse.Namespace) -> int:
    before = client.ai.assign_llm(profile, agents)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{args.instance or 'appliance'}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(before, indent=2, default=str), encoding="utf-8")
    print(f"{len(agents or before)} agent(s) now on {profile!r}; previous assignments saved to {path}")
    print(f"undo with: pyfsr llm restore {path}")
    return 0


def cmd_assign(args: argparse.Namespace) -> int:
    return _assign(_client(args), args.profile, args.agent or None, args)


def cmd_restore(args: argparse.Namespace) -> int:
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    _client(args).ai.restore_llm_assignments(snapshot)
    print(f"restored {len(snapshot)} agent assignment(s)")
    return 0


def _common(p: argparse.ArgumentParser) -> None:
    from .playbook import add_connection_args

    add_connection_args(p)
    p.add_argument("--instance", help="alias from ~/.pyfsr/instances.toml (overrides the connection flags)")


def build_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("status", help="reasoning profiles, wizard providers, each agent's profile, FortiAI balance")
    _common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("setup", help="add a third-party LLM as a reasoning profile (via its connector)")
    _common(p)
    p.add_argument("--profile", required=True, help="reasoning-profile name, e.g. 'OpenAI GPT-4.1'")
    p.add_argument("--connector", default="openai", help="LLM connector (default openai)")
    p.add_argument("--model", required=True, help="the connector's model option, e.g. GPT-4.1")
    p.add_argument("--config-name", help="connector configuration name (default: the profile name)")
    p.add_argument("--api-key-env", default="OPENAI_API_KEY", help="env var holding the API key")
    p.add_argument("--api-key-file", help="file holding the API key (instead of --api-key-env)")
    p.add_argument("--azure-endpoint", help="Azure OpenAI endpoint (switches the connector to Azure)")
    p.add_argument("--azure-deployment", help="Azure OpenAI deployment id")
    p.add_argument("--azure-api-version", default="2024-12-01-preview")
    p.add_argument("--no-install", action="store_true", help="don't install the connector from Content Hub")
    p.add_argument("--no-verify", action="store_true", help="skip the one-line test completion")
    p.add_argument("--assign", action="store_true", help="also switch every agent to the new profile")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("assign", help="switch agents to a reasoning profile (saves a snapshot to undo)")
    _common(p)
    p.add_argument("profile", help="profile name or uuid")
    p.add_argument("--agent", action="append", help="agent name (repeatable; default: every agent)")
    p.set_defaults(func=cmd_assign)

    p = sub.add_parser("restore", help="put agents back from a snapshot written by assign")
    _common(p)
    p.add_argument("snapshot", help="snapshot JSON")
    p.set_defaults(func=cmd_restore)
