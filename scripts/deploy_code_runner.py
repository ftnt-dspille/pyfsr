#!/usr/bin/env python
"""Deploy the custom ``code-runner`` connector to a FortiSOAR appliance.

``code-runner`` is an UNRESTRICTED Python-exec connector (top-level ``return``
works; ``open()``/imports unrestricted) used by the reconcile-and-report
archetype's diff/CSV step in place of the stock sandboxed ``code-snippet``.

Because it is an uncertified (``cs_approved: false``) custom connector, the
appliance must have the *custom connector* gate ON before it can be installed
and executed. In FortiSOAR that gate is the ``allowCustomConnector`` flag
(System Settings -> Application Editor -> Advanced Development Settings) -- the
SAME flag that gates custom code execution. pyfsr exposes it as
``system_settings.set_development_mode(connectors=True)``.

This script (idempotent):
  1. asserts/sets ``allowCustomConnector`` ON (prints the before/after state),
  2. packs + installs ``connector-code-runner/code-runner`` (replace=True, waits),
  3. creates a "Default" configuration (the connector has no config fields),
  4. runs a trivial health snippet to prove unrestricted exec works live.

Env: BASE_URL (required, e.g. https://fortisoar.example.com), FSR_USERNAME
(default csadmin), FSR_PASSWORD (required), FSR_VERIFY_SSL (default false for
self-signed appliance certs). A local .env file (gitignored) is the easy way::

    set -a; . ./.env.testing; set +a
    .venv/bin/python scripts/deploy_code_runner.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pyfsr import FortiSOAR

CONNECTOR_DIR = Path(__file__).resolve().parent.parent.parent / "connector-code-runner" / "code-runner"


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    base_url = os.environ.get("BASE_URL") or os.environ.get("FSR_BASE_URL")
    username = os.environ.get("FSR_USERNAME", "csadmin")
    password = os.environ.get("FSR_PASSWORD")
    verify = _bool_env("FSR_VERIFY_SSL", False)
    if not base_url:
        print("BASE_URL (or FSR_BASE_URL) is required", file=sys.stderr)
        return 2
    if not password:
        print("FSR_PASSWORD is required", file=sys.stderr)
        return 2
    if not (CONNECTOR_DIR / "info.json").exists():
        print(f"connector source not found: {CONNECTOR_DIR}", file=sys.stderr)
        return 2

    client = FortiSOAR(base_url, username=username, password=password, verify_ssl=verify)
    print(f"connected to {client.base_url} (version {client.version()})\n")

    # 1. custom-connector gate (allowCustomConnector) -----------------------
    # The "Advanced Development Settings" record (allowCustomConnector) is an 8.0
    # construct. On 7.6.x it does not exist and custom connectors install without
    # it, so this step is best-effort: enable when present, skip otherwise.
    try:
        before = client.system_settings.get_development_mode()
        print(f"development mode (before): {before}")
        if not before.get("connectors"):
            client.system_settings.set_development_mode(connectors=True)
            after = client.system_settings.get_development_mode()
            print(f"development mode (after):  {after}")
            if not after.get("connectors"):
                print("FAILED to enable allowCustomConnector", file=sys.stderr)
                return 1
        else:
            print("allowCustomConnector already ON")
    except ValueError as e:
        print(f"dev-mode gate not present (7.6.x, custom connectors allowed by default): {e}")

    # 2. install the connector ---------------------------------------------
    print(f"\ninstalling {CONNECTOR_DIR.name} from {CONNECTOR_DIR} ...")
    status = client.connectors.install_from_dir(str(CONNECTOR_DIR), replace=True, wait=True)
    print(f"install status: {status}")

    # 3. create a Default configuration (no config fields) ------------------
    cfg = client.connectors.upsert_configuration(
        "code-runner",
        name="Default",
        config={},
        version="1.0.0",
    )
    print(f"\nconfiguration: {cfg.get('name') if isinstance(cfg, dict) else cfg}")

    # 4. live health: prove unrestricted exec (top-level return) -----------
    health = client.connectors.execute("code-runner", "run_python", params={"code": "return {'ok': True}"})
    print(f"\nlive run_python -> {health}")
    print("\ncode-runner deployed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
