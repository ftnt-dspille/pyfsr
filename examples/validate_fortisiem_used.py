"""Validate that a FortiSIEM MCP tool was actually used during an investigation.

Closes the evidence loop for the FortiSIEM <-> FortiAI integration: given an
alert (or a known triage ``task_id``), prove that one of FortiSIEM's MCP tools
was invoked while the agents investigated it.

How the linkage works (all verified live):

  alert ──investigate_alert()──▶ task_id   ( == correlationID )
                                    │
            llm_activity_logs?correlationID=<task_id>
                                    │
                       response.tool_name + tool_args

  * The triage ``task_id`` IS the ``correlationID`` stamped on every
    ``llm_activity_logs`` record for that run.
  * The alert's ``triagetaskid`` field holds the *latest* run's ``task_id``
    (``client.ai.get_investigation_for_alert``). To find *every* past run we
    full-text search the logs for the alert uuid and read back the distinct
    ``correlationID``\\ s (``client.ai.find_investigations``).
  * Which tool ran lives in ``response.tool_name`` — NOT the prompt text.

Attribution to FortiSIEM: we ask the live FortiSIEM MCP server for its tool
catalogue (``list_mcp_tools``, after minting a fresh bearer token) and intersect
it with the tools the investigation actually called.

Usage:
    python examples/validate_fortisiem_used.py --alert <alert-uuid>
    python examples/validate_fortisiem_used.py --task-id <task-id>

Env (same as connect_fortisiem_mcp.py):
    FSR_BASE_URL, FSR_USERNAME, FSR_PASSWORD
    FORTISIEM_BASE_URL, FORTISIEM_CLIENT_ID, FORTISIEM_CLIENT_SECRET
"""

from __future__ import annotations

import argparse
import sys

# Reuse the token-mint + MCP-config helpers from the registration example.
from connect_fortisiem_mcp import (  # type: ignore
    FSR_BASE_URL,
    FSR_PASSWORD,
    FSR_USERNAME,
    build_config,
    mint_fortisiem_token,
)

from pyfsr import FortiSOAR


def fortisiem_tool_names(client: FortiSOAR) -> set[str]:
    """Ask the live FortiSIEM MCP server for the tools it advertises."""
    config = build_config(mint_fortisiem_token())
    return set(client.ai.list_mcp_tools(config))


def resolve_task_ids(client: FortiSOAR, *, alert: str | None, task_id: str | None) -> list[str]:
    """Return the task_id(s) to check — given directly or recovered from the alert."""
    if task_id:
        return [task_id]
    found = client.ai.find_investigations(alert)
    if not found:
        sys.exit(f"No investigations found for alert {alert!r} in the activity logs.")
    print(f"Found {len(found)} past investigation(s) of alert {alert}:")
    for f in found:
        print(f"  task_id={f['task_id']}  ({f['log_count']} log records)")
    return [f["task_id"] for f in found]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--alert", help="alert uuid (recovers task_id from the logs)")
    group.add_argument("--task-id", help="a known triage task_id (== correlationID)")
    args = ap.parse_args()

    client = FortiSOAR(
        base_url=FSR_BASE_URL,
        auth=(FSR_USERNAME, FSR_PASSWORD),
        verify_ssl=False,
        suppress_insecure_warnings=True,
    )

    siem_tools = fortisiem_tool_names(client)
    print(f"\nFortiSIEM advertises {len(siem_tools)} tools: {sorted(siem_tools)}\n")

    task_ids = resolve_task_ids(client, alert=args.alert, task_id=args.task_id)

    overall_ok = False
    for tid in task_ids:
        calls = client.ai.investigation_tool_calls(tid)
        used = [c["tool_name"] for c in calls]
        siem_used = sorted({t for t in used if t in siem_tools})
        status = "PASS" if siem_used else "FAIL"
        overall_ok = overall_ok or bool(siem_used)
        print(f"[{status}] investigation {tid}")
        print(f"    tools called : {sorted(set(used))}")
        print(f"    FortiSIEM    : {siem_used or '(none — no FortiSIEM tool was used)'}")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
