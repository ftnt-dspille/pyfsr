"""FortiSIEM <-> FortiAI MCP: one-file setup + test, all through pyfsr.

This single example does the whole loop against a FortiSOAR 8.0 appliance:

  setup  -- register FortiSIEM's MCP server with FortiAI and grant it to the
            triage agents (mint OAuth2 token, validate, save, bind allowlists)
  test   -- run a FortiAI investigation on a FortiSIEM-sourced alert, wait for
            it to finish, then PROVE a FortiSIEM MCP tool was actually called
  refresh -- re-mint the ~24h FortiSIEM bearer token onto the saved server

Why a static bearer: FortiSIEM exposes a streamable-HTTP MCP server at
``/phoenix/mcp`` behind an OAuth2 ``client_credentials`` grant, but FortiSOAR's
MCP client only forwards a *static* credential. So we mint the token here and
store it as the registered server's bearer value (re-run ``refresh`` when it
expires).

Evidence linkage (verified live): a triage ``task_id`` IS the ``correlationID``
stamped on every ``llm_activity_logs`` record for that run; ``response.tool_name``
names the tool the agent picked. We intersect the tools the run actually called
with the live FortiSIEM tool catalogue to attribute usage to FortiSIEM.

Configure via env (or edit the constants below):
  FSR_BASE_URL, FSR_USERNAME, FSR_PASSWORD         -> FortiSOAR appliance
  FORTISIEM_BASE_URL                               -> https://fortisiem.example.com:13001/phoenix
  FORTISIEM_CLIENT_ID, FORTISIEM_CLIENT_SECRET     -> FortiSIEM API token values

Usage:
  python examples/fortisiem_mcp_setup_and_test.py setup
  python examples/fortisiem_mcp_setup_and_test.py test  --alert <alert-uuid>
  python examples/fortisiem_mcp_setup_and_test.py test                 # auto-pick a FortiSIEM alert
  python examples/fortisiem_mcp_setup_and_test.py refresh --uuid <mcp-uuid>

The deeper investigation/audit tooling (full tool-I/O reports, hypothesis
provenance, influence trials, recorded runs) lives alongside this file's archive
in Miscellaneous/fortisoar/fortisiem-mcp/.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

from pyfsr import FortiSOAR

FSR_BASE_URL = os.environ.get("FSR_BASE_URL", "fortisoar.example.com:13002")
FSR_USERNAME = os.environ.get("FSR_USERNAME", "csadmin")
FSR_PASSWORD = os.environ.get("FSR_PASSWORD", "changeme")

FSIEM_BASE = os.environ.get("FORTISIEM_BASE_URL", "https://fortisiem.example.com:13001/phoenix").rstrip("/")
FSIEM_CLIENT_ID = os.environ.get("FORTISIEM_CLIENT_ID", "2e6920c8-a148-4a2e-b592-65a7c3c2418c")
FSIEM_CLIENT_SECRET = os.environ.get("FORTISIEM_CLIENT_SECRET", "")

MCP_NAME = "FortiSIEM"
MCP_URL = f"{FSIEM_BASE}/mcp"
TOKEN_URL = f"{FSIEM_BASE}/rest/pub/security/oauth/token"
FSIEM_SOURCE = "Fortinet FortiSIEM"

# Evidence-gathering agents that should be allowed to call FortiSIEM during
# triage. ``alert-investigation`` is the orchestrator (no MCP config of its own);
# it reaches SIEM through these sub-agents.
TARGET_AGENTS = [
    ("siem", "1.0.0"),
    ("ioc-enrichment", "1.0.0"),
    ("asset-enrichment", "1.0.0"),
    ("endpoint-telemetry", "1.0.0"),
    ("investigation-planning", "1.0.0"),
]


def connect() -> FortiSOAR:
    return FortiSOAR(
        base_url=FSR_BASE_URL,
        auth=(FSR_USERNAME, FSR_PASSWORD),
        verify_ssl=False,
        suppress_insecure_warnings=True,
    )


def mint_fortisiem_token() -> str:
    """OAuth2 client_credentials grant against FortiSIEM (POST form)."""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": FSIEM_CLIENT_ID,
            "client_secret": FSIEM_CLIENT_SECRET,
        },
        verify=False,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def build_config(token: str) -> dict:
    # Field shapes mirror the FortiSOAR UI's add-MCP-server form exactly:
    #   type="external" (user server), transport="http", and a BEARER auth object
    #   {"value", "type"} that the save step JSON-encodes.
    return {
        "name": MCP_NAME,
        "description": "FortiSIEM Phoenix MCP server (incidents, context, reputation, IOCs)",
        "type": "external",  # persisted record field; built-ins are "internal"
        "transport": "http",  # FortiSOAR maps http -> streamable_http
        "url": MCP_URL,
        "active": True,
        "authentication": {"value": token, "type": "BEARER"},
    }


def setup(client: FortiSOAR) -> str:
    """Register FortiSIEM's MCP server and bind it to the triage agents."""
    if not client.ai.features_enabled():
        client.ai.enable_features(modified_by="pyfsr")
        print("Enabled FortiAI features.")

    config = build_config(mint_fortisiem_token())

    # 1. Probe the live server — confirms auth + lists the tools it offers.
    validation = client.ai.validate_mcp_server(config)
    tools = validation.get("tools") or []
    print(f"Validation: valid={validation.get('valid')} tools={len(tools)}")
    for t in tools:
        print(f"  - {t.get('name')}")

    # 2. Save it the way the UI does — validate-then-save. Reuse an existing row
    #    with the same name (update via uuid) so this is re-runnable.
    existing = {m.get("name"): m.get("id") for m in client.ai.list_mcp_servers()}
    if MCP_NAME in existing:
        config["uuid"] = existing[MCP_NAME]
    saved = client.ai.save_mcp_server(config)
    mcp_uuid = saved.get("uuid") or existing.get(MCP_NAME) or saved.get("@id", "").split("/")[-1]
    print(f"Saved MCP server {MCP_NAME} ({mcp_uuid})")

    # 3. Bind to each target agent's allowlist.
    for name, version in TARGET_AGENTS:
        try:
            client.ai.allow_mcp_server_for_agent(name, version, mcp_uuid)
            allowed = client.ai.list_agent_mcp_servers(name, version)
            friendly = client.ai.list_agent_mcp_servers(name, version, friendly=True)
            ok = "OK" if mcp_uuid in allowed else "MISSING"
            print(f"  [{ok}] {name} -> mcp_servers={friendly}")
        except Exception as exc:  # noqa: BLE001 - keep going across agents
            print(f"  [SKIP] {name}: {exc}")

    print(f"\nSetup done. FortiSIEM MCP uuid = {mcp_uuid}")
    return mcp_uuid


def refresh(client: FortiSOAR, mcp_uuid: str) -> None:
    """Re-mint the FortiSIEM token and write it back onto the registered server."""
    client.ai.update_mcp_server(mcp_uuid, build_config(mint_fortisiem_token()))
    print(f"Refreshed FortiSIEM token on MCP server {mcp_uuid}")


def _pick_fortisiem_alert(client: FortiSOAR) -> str:
    """Return the uuid of the most recent FortiSIEM-sourced alert."""
    resp = client.get(
        "/api/3/alerts",
        params={"source": FSIEM_SOURCE, "$limit": 1, "$orderby": "-createDate"},
    )
    rows = resp.get("hydra:member") or resp.get("data") or []
    if not rows:
        sys.exit(f"No alerts with source={FSIEM_SOURCE!r} found — create one first.")
    return rows[0]["@id"].split("/")[-1] if "@id" in rows[0] else rows[0]["id"]


def _poll(client: FortiSOAR, task_id: str, *, interval: float = 5.0, timeout: float = 600.0) -> str:
    """Print status transitions until the investigation reaches a terminal state."""
    from pyfsr.api.ai import TERMINAL_STATUSES

    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        status = client.ai.get_status(task_id)
        if status != last:
            print(f"  status: {status}")
            last = status
        if status in TERMINAL_STATUSES:
            return status
        time.sleep(interval)
    print("  (timed out waiting for a terminal status)")
    return last or ""


def test(client: FortiSOAR, alert: str | None) -> bool:
    """Investigate a FortiSIEM alert and prove a FortiSIEM MCP tool was used."""
    if not client.ai.features_enabled():
        sys.exit("FortiAI features are disabled — run `setup` first.")

    alert = alert or _pick_fortisiem_alert(client)
    print(f"Investigating alert {alert}")

    # Reuse a prior run if one exists for this alert; else start one and wait.
    task_id = client.ai.get_investigation_for_alert(alert)
    if task_id:
        print(f"  reusing investigation task_id={task_id}")
        client.ai.get_status(task_id)
    else:
        started = client.ai.start_alert_investigation(alert)  # links task_id to the alert
        task_id = started["task_id"]
        print(f"  task_id={task_id}")
        _poll(client, task_id)

    # The live FortiSIEM tool catalogue (mint a fresh token to ask it).
    siem_tools = set(client.ai.list_mcp_tools(build_config(mint_fortisiem_token())))
    print(f"\nFortiSIEM advertises {len(siem_tools)} tools: {sorted(siem_tools)}")

    # Which tools did the agents actually call?
    calls = client.ai.investigation_tool_calls(task_id)
    used = sorted({c["tool_name"] for c in calls})
    siem_used = sorted(t for t in used if t in siem_tools)
    status = "PASS" if siem_used else "FAIL"
    print(f"\n[{status}] investigation {task_id}")
    print(f"    tools called : {used}")
    print(f"    FortiSIEM    : {siem_used or '(none — no FortiSIEM tool was used)'}")
    return bool(siem_used)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="register FortiSIEM MCP server + bind to agents")
    t = sub.add_parser("test", help="investigate a FortiSIEM alert and prove a SIEM tool was used")
    t.add_argument("--alert", help="alert uuid (default: most recent FortiSIEM-sourced alert)")
    r = sub.add_parser("refresh", help="re-mint the FortiSIEM bearer token onto the saved server")
    r.add_argument("--uuid", required=True, help="the saved MCP server uuid")
    args = ap.parse_args()

    client = connect()
    if args.cmd == "setup":
        setup(client)
    elif args.cmd == "refresh":
        refresh(client, args.uuid)
    elif args.cmd == "test":
        sys.exit(0 if test(client, args.alert) else 1)


if __name__ == "__main__":
    main()
