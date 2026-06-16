"""Register FortiSIEM's MCP server with FortiAI and grant it to the triage agents.

FortiSIEM exposes a streamable-HTTP MCP server at ``/phoenix/mcp``, authenticated
with an OAuth2 ``client_credentials`` *bearer* token minted from the FortiSIEM
public REST API. FortiSOAR's MCP client only forwards a **static** credential
(it has ``bearer``/``api_key``/``basic``/``fsr`` auth, but no OAuth grant of its
own), so we mint the token here and store it as the registered server's bearer
value — and re-run :func:`refresh_token` when it expires (~24h).

What this does, all through ``client.ai``:
  1. mint a FortiSIEM bearer token (OAuth2 client_credentials)
  2. validate the MCP config against the live server (enumerates its tools)
  3. register it as an MCP server in FortiSOAR
  4. append its uuid to each target agent's ``config["mcp_server"]`` allowlist

Configure via env (or edit the constants below):
  FSR_BASE_URL, FSR_USERNAME, FSR_PASSWORD         -> FortiSOAR appliance
  FORTISIEM_BASE_URL                               -> e.g. https://10.99.248.120:13001/phoenix
  FORTISIEM_CLIENT_ID, FORTISIEM_CLIENT_SECRET     -> FortiSIEM API token values
"""

from __future__ import annotations

import os

import httpx

from pyfsr import FortiSOAR

FSR_BASE_URL = os.environ.get("FSR_BASE_URL", "10.99.249.159:13002")
FSR_USERNAME = os.environ.get("FSR_USERNAME", "csadmin")
FSR_PASSWORD = os.environ.get("FSR_PASSWORD", "fortinet")

FSIEM_BASE = os.environ.get("FORTISIEM_BASE_URL", "https://10.99.248.120:13001/phoenix").rstrip("/")
FSIEM_CLIENT_ID = os.environ.get("FORTISIEM_CLIENT_ID", "2e6920c8-a148-4a2e-b592-65a7c3c2418c")
FSIEM_CLIENT_SECRET = os.environ.get("FORTISIEM_CLIENT_SECRET", "")

MCP_NAME = "FortiSIEM"
MCP_URL = f"{FSIEM_BASE}/mcp"
TOKEN_URL = f"{FSIEM_BASE}/rest/pub/security/oauth/token"

# Evidence-gathering agents that should be allowed to call FortiSIEM during
# triage. The `alert-investigation` agent is the orchestrator (no MCP config of
# its own); it reaches SIEM through these sub-agents.
TARGET_AGENTS = [
    ("siem", "1.0.0"),
    ("ioc-enrichment", "1.0.0"),
    ("asset-enrichment", "1.0.0"),
    ("endpoint-telemetry", "1.0.0"),
    ("investigation-planning", "1.0.0"),
]


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


def refresh_token(client: FortiSOAR, mcp_uuid: str) -> None:
    """Re-mint the FortiSIEM token and write it back onto the registered server.

    Run on a schedule (the token lives ~24h). ``register_mcp_server`` returns the
    uuid you pass here.
    """
    client.ai.update_mcp_server(mcp_uuid, build_config(mint_fortisiem_token()))
    print(f"Refreshed FortiSIEM token on MCP server {mcp_uuid}")


def main() -> None:
    client = FortiSOAR(
        base_url=FSR_BASE_URL,
        auth=(FSR_USERNAME, FSR_PASSWORD),
        verify_ssl=False,
        suppress_insecure_warnings=True,
    )

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

    print(f"\nDone. FortiSIEM MCP uuid = {mcp_uuid}")


if __name__ == "__main__":
    main()
