"""Prove the FortiSIEM MCP binding by inspecting tool-usage evidence.

After binding FortiSIEM to the triage agents (see connect_fortisiem_mcp.py), run
an investigation and show that the SIEM agent's "Tool Selection" LLM calls were
offered — and ideally chose — FortiSIEM tools. The `llm_activity_logs` module is
the queryable audit trail: each record's `prompt` carries the tool catalogue the
agent was given (filtered by its mcp_server allowlist) and `response` carries the
tool it picked.

Baseline ("before"): any llm_activity_logs created before FortiSIEM was
registered contain zero FortiSIEM tool names. This script captures the "after".
"""

from __future__ import annotations

import json
import os
import sys
import time

from pyfsr import FortiSOAR

FSIEM_TOOLS = [
    "get_incidents_by_entity",
    "get_incident_by_id",
    "get_context_by_entity",
    "get_reputation_by_entity",
    "get_iocs_by_incident_ids",
    "get_related_incidents_by_id",
    "get_trigger_events_by_incident_id",
    "query_fsm_postgres",
    "query_fsm_clickhouse",
    "get_top_10_risky_users_incidents",
    "get_top_10_risky_devices_incidents",
]


def scan_logs(client: FortiSOAR, since_epoch: float) -> list[dict]:
    """Return llm_activity_logs created since `since_epoch` that mention a SIEM tool."""
    rows = client.get(
        "/api/3/llm_activity_logs",
        params={"$limit": 200, "$orderby": "-createDate"},
    ).get("hydra:member", [])
    hits = []
    for m in rows:
        if (m.get("createDate") or 0) < since_epoch:
            continue
        blob = json.dumps(m.get("prompt"), default=str) + json.dumps(m.get("response"), default=str)
        offered = [t for t in FSIEM_TOOLS if t in blob]
        if offered:
            hits.append(
                {
                    "title": m.get("title"),
                    "model": m.get("modelName"),
                    "correlationID": m.get("correlationID"),
                    "siem_tools_seen": offered,
                }
            )
    return hits


def main() -> None:
    alert_uuid = sys.argv[1] if len(sys.argv) > 1 else "740a751c"
    client = FortiSOAR(
        base_url=os.environ.get("FSR_BASE_URL", "10.99.249.159:13002"),
        auth=(
            os.environ.get("FSR_USERNAME", "csadmin"),
            os.environ.get("FSR_PASSWORD", "fortinet"),
        ),
        verify_ssl=False,
        suppress_insecure_warnings=True,
    )

    # Confirm the binding is in place first.
    for n in ("siem", "ioc-enrichment", "asset-enrichment"):
        print(f"  {n}: {client.ai.list_agent_mcp_servers(n, '1.0.0', friendly=True)}")

    start = time.time()
    print(f"\nStarting investigation on alert {alert_uuid} ...")
    report = client.ai.investigate_alert(alert_uuid, wait=True, interval=8, timeout=900)
    print("Status:", report.get("status"))

    print("\nScanning llm_activity_logs for FortiSIEM tool usage ...")
    hits = scan_logs(client, since_epoch=start - 5)
    if hits:
        print(f"EVIDENCE — FortiSIEM tools reached the agents in {len(hits)} LLM call(s):")
        for h in hits:
            print(f"  [{h['title']}] saw {h['siem_tools_seen']}")
    else:
        print("No FortiSIEM tool mentions found in this run's LLM activity logs.")


if __name__ == "__main__":
    main()
