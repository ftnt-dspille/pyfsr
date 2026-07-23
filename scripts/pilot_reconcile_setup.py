#!/usr/bin/env python
"""Archetype pilot setup — install/configure connectors + smoke-test the source ops.

Replaces the mock inventories in the reconcile-and-report pilot with the REAL
ServiceNow CMDB + FortiCloud Asset Management connectors. This is the de-risk
gate before authoring the real-connector playbook: it (1) installs the missing
servicenow-cmdb connector if needed, (2) configures both connectors from the
creds in ``.env.pilot`` (validated + idempotent), and (3) executes each source
operation live to prove the creds authenticate AND to capture the real output
shape the playbook's diff step will consume.

Env: ``.env.pilot`` (gitignored). Source it first, or let this script load it::

    set -a; . ./.env.pilot; set +a
    .venv/bin/python scripts/pilot_reconcile_setup.py

Both smoke-test ops are READS (list/get) ��� non-mutating against the external
services. Nothing here writes to ServiceNow or FortiCloud.

Box: .205 (7.6.5-5662), session auth (csadmin) — sidesteps the api-key encrypt
regression. See memory: archetype-recipe-framework, apikey-create-encrypt-regression.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Run from the repo root without installing pyfsr.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pyfsr import FortiSOAR  # noqa: E402
from pyfsr.config import EnvConfig  # noqa: E402
from pyfsr.exceptions import FortiSOARException  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / ".env.pilot"


# --------------------------------------------------------------------- env load
def load_env_file(path: Path) -> None:
    """Load a ``KEY=VALUE`` env file into ``os.environ`` (existing vars win).

    Stdlib only — no python-dotenv dependency. Skips comments / blanks; a
    leading/trailing ``#`` line is ignored. Values are taken literally (no
    shell expansion), so quote-free secrets with special chars are fine.
    """
    if not path.exists():
        sys.exit(f"env file not found: {path} (create it from the template in this script's header)")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if key and key not in os.environ:  # real env vars override the file
            os.environ[key] = val


def env(name: str, *, required: bool = True) -> str:
    v = os.environ.get(name, "").strip()
    if required and not v:
        sys.exit(f"missing required env var {name} — fill it in {ENV_FILE}")
    return v


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() not in {"0", "false", "no", "off", ""}


# --------------------------------------------------------------- connector defs
# (name, config_label, config_dict) — config fields read live off .205 on
# 2026-06-26; each maps 1:1 to the connector's config_schema. Built in main()
# AFTER the env file is loaded (env() reads os.environ).
def build_specs() -> list[dict]:
    return [
        {
            "name": "servicenow-cmdb",
            "config_name": env("SERVICENOW_CONFIG_NAME"),
            "config": {
                "server_url": env("SERVICENOW_SERVER_URL"),
                "username": env("SERVICENOW_USERNAME"),
                "password": env("SERVICENOW_PASSWORD"),
                "verify_ssl": env_bool("SERVICENOW_VERIFY_SSL", True),
            },
            # smoke-test op: Get Configuration Items (read)
            "smoke": {
                "operation": "get_configuration_items",
                "params": {"class_name": "cmdb_ci", "sysparm_limit": 5},
            },
        },
        {
            "name": "forticloud-asset-management",
            "config_name": env("FORTICLOUD_CONFIG_NAME"),
            "config": {
                "server_url": env("FORTICLOUD_SERVER_URL"),
                "api_id": env("FORTICLOUD_API_ID"),
                "password": env("FORTICLOUD_PASSWORD"),
                "client_id": env("FORTICLOUD_CLIENT_ID"),
                "verify_ssl": env_bool("FORTICLOUD_VERIFY_SSL", True),
            },
            # smoke-test: the connector's list_assets op (v1.0.0) is BROKEN ��� it POSTs
            # {serialNumber, expireBefore} to /products/list but never sends accountId,
            # which the FortiCare API requires for Org-scope API users (yours is Org-
            # scope). So we BYPASS list_assets via generic_api_call -> POST /products/list
            # with the full payload per the API spec (accountId is mandatory here).
            # This same bypass is what the pilot playbook's FortiCloud step must use.
            "smoke": {
                "operation": "generic_api_call",
                "params": {
                    "method": "POST",
                    "endpoint": "/products/list",
                    "payload": {
                        "accountId": env("FORTICLOUD_ACCOUNT_ID", required=False),
                        "expireBefore": "2030-01-01T00:00:00-00:00",
                        "status": "Registered",
                    },
                },
                # accountId is the one value the connector can't supply or derive
                # (not in config, not an op param, not in the OAuth token response).
                "required_env": ["FORTICLOUD_ACCOUNT_ID"],
            },
        },
    ]


def _show(data: object, limit: int = 800) -> str:
    """Compact JSON preview of an op result (for the captured-shape report)."""
    try:
        return json.dumps(data, default=str)[:limit]
    except TypeError:
        return str(data)[:limit]


def main() -> int:
    load_env_file(ENV_FILE)
    client: FortiSOAR = EnvConfig.from_env().client()
    conn = client.connectors
    print(f"connected to {client.base_url} (version {client.version()})\n")

    results = []
    for spec in build_specs():
        name, label = spec["name"], spec["config_name"]
        print(f"===== {name} =====")

        # 1+2. ensure installed (Content-Hub install if missing) AND configured.
        #      Idempotent: install is skipped when present, the named config is
        #      upserted; default=True so a config-less connector step picks it up.
        try:
            cfg = conn.ensure_configured(
                name,
                spec["config"],
                config_name=label,
                version="1.0.0",
                default=True,
                validate=True,
                autofill=True,
            )
            print(f"  configured as {label!r} (config_id={cfg.config_id}, default={cfg.default})")
        except FortiSOARException as e:
            print(f"  CONFIGURE FAILED: {e}")
            results.append((name, "configure", False, str(e)))
            continue

        # 3. smoke-test the source op (read) — proves the creds authenticate and
        #    captures the real output shape for the playbook's diff step.
        op = spec["smoke"]["operation"]
        params = spec["smoke"]["params"]
        missing = [v for v in spec["smoke"].get("required_env", []) if not os.environ.get(v, "").strip()]
        if missing:
            hint = ", ".join(missing)
            print(
                f"  smoke SKIPPED ({op}): set {hint} in {ENV_FILE.name} (the connector "
                f"can't supply it). Configure + auth still verified above."
            )
            results.append((name, op, False, f"needs {hint} in .env.pilot"))
            print()
            continue
        try:
            r = conn.execute(name, op, config=label, params=params)
            msg = r.message or ""
            print(f"  smoke {op} -> status={r.status} msg={msg!r}")
            print(f"    output preview: {_show(r.data)}")
            results.append((name, op, r.ok, msg or ("success" if r.ok else f"status={r.status}")))
        except FortiSOARException as e:
            print(f"  SMOKE FAILED ({op}): {e}")
            results.append((name, op, False, str(e)))
        print()

    # ---- summary
    print("=" * 60)
    print("SETUP SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, step, ok, detail in results:
        flag = "OK  " if ok else "FAIL"
        print(f"  [{flag}] {name:32} {step:28} {detail[:60]}")
        all_ok = all_ok and ok
    print()
    if all_ok:
        print("Both connectors configured + creds verified. Ready to author the")
        print("real-connector pilot YAML (forticloud list_assets -> servicenow")
        print("get_configuration_items -> diff -> create_record -> email).")
        return 0
    print("One or more steps failed — fix above before authoring the playbook.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
