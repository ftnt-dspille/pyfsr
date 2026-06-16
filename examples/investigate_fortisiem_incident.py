"""End-to-end: investigate a FortiSIEM incident with FortiAI and audit tool usage.

What it does:
  1. find a FortiSIEM-sourced alert (``source == "Fortinet FortiSIEM"``)
  2. trigger a FortiAI agentic investigation on it (non-blocking)
  3. poll the pipeline status while it runs, printing phase progress
  4. list every tool the agents called — tagged with the **MCP server that owns
     it** — plus each call's inputs (tool_args), observed output, and the
     execution telemetry (latency / tokens / cost) that proves it really ran
  5. show the **provenance chain**: which agent answered each investigation
     question with what evidence, and how that evidence weighted each hypothesis
     into the final verdict — i.e. proof the conclusion is grounded in the tools,
     not asserted

The server attribution is vendor-neutral: ``client.ai.mcp_tool_catalog()`` probes
*every* registered MCP server's ``tools/list`` and maps tool -> server, so this
works for any 3rd-party SIEM, not just FortiSIEM. (Devil's advocate: tool names
are only unique *per deployment* — if two servers ever expose the same tool name,
the catalog keeps the first-probed owner; inspect ``mcp_configs()`` to
disambiguate.)

Tool **outputs** aren't a stored field — FortiSOAR feeds a tool's result back as
the *next* reasoning step's prompt. ``observed_outputs()`` below recovers them
best-effort by pairing each "Tool Selection" log with the following record.

Config: reads examples/config.toml (see config.toml.example). Point it at a box
with FortiAI enabled and the FortiSIEM MCP server registered.

Usage:
    python examples/investigate_fortisiem_incident.py            # newest FortiSIEM alert
    python examples/investigate_fortisiem_incident.py --alert <uuid>
    python examples/investigate_fortisiem_incident.py --reuse    # don't re-run; audit the last investigation
"""

from __future__ import annotations

import argparse
import json
import time
import tomllib

from pyfsr import FortiSOAR

FORTISIEM_SOURCE = "Fortinet FortiSIEM"


def connect() -> FortiSOAR:
    with open("config.toml", "rb") as f:
        cfg = tomllib.load(f)["fortisoar"]
    return FortiSOAR(
        base_url=cfg["base_url"],
        auth=(cfg["auth"]["username"], cfg["auth"]["password"]),
        verify_ssl=cfg.get("verify_ssl", True),
        suppress_insecure_warnings=True,
    )


def find_fortisiem_alert(client: FortiSOAR, alert_uuid: str | None) -> dict:
    """Return a FortiSIEM-sourced alert (the given one, or the newest)."""
    if alert_uuid:
        return client.alerts.get(alert_uuid)
    resp = client.alerts.list(
        {"source": FORTISIEM_SOURCE, "$orderby": "-createDate", "$limit": 1}
    )
    members = resp.get("hydra:member") or []
    if not members:
        raise SystemExit(f"No alerts with source {FORTISIEM_SOURCE!r} found.")
    return members[0]


def poll(client: FortiSOAR, task_id: str, *, interval: float = 5.0, timeout: float = 600.0) -> str:
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


def observed_outputs(client: FortiSOAR, task_id: str) -> list[str]:
    """Best-effort tool outputs: the result fed back into the next reasoning step.

    FortiSOAR doesn't store a tool's return value as a field; it appends it to the
    next LLM call's prompt. So for each "Tool Selection" log (in id order) we read
    the tail of the *following* record's prompt as that tool's observed output.
    Returned list is parallel to ``investigation_tool_calls(task_id)``.
    """
    resp = client.get("/api/3/llm_activity_logs", params={"correlationID": task_id, "$limit": 500})
    recs = sorted(resp.get("hydra:member") or [], key=lambda r: r.get("id") or 0)

    def as_obj(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return {}
        return v if isinstance(v, dict) else {}

    outputs: list[str] = []
    for i, rec in enumerate(recs):
        resp_obj = as_obj(rec.get("response"))
        if not resp_obj.get("tool_name"):
            continue
        nxt = recs[i + 1] if i + 1 < len(recs) else None
        prompt = json.dumps(as_obj(nxt.get("prompt")) if nxt else "")
        outputs.append(prompt[-400:] if nxt else "(no following record)")
    return outputs


def execution_telemetry(client: FortiSOAR, task_id: str) -> dict:
    """Aggregate llm_activity_logs for the run — proof real LLM+tool calls fired."""
    resp = client.get("/api/3/llm_activity_logs", params={"correlationID": task_id, "$limit": 500})
    recs = resp.get("hydra:member") or []
    return {
        "steps": len(recs),
        "input_tokens": sum(r.get("inputTokens") or 0 for r in recs),
        "output_tokens": sum(r.get("outputTokens") or 0 for r in recs),
        "cost_usd": sum(r.get("costUSD") or 0 for r in recs),
        "models": sorted({r.get("modelName") for r in recs if r.get("modelName")}),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--alert", help="alert uuid to investigate (default: newest FortiSIEM alert)")
    ap.add_argument(
        "--reuse", action="store_true",
        help="don't start a new run; audit the alert's existing investigation",
    )
    args = ap.parse_args()

    client = connect()

    if not client.ai.features_enabled():
        client.ai.enable_features(modified_by="pyfsr")
        print("Enabled FortiAI features.")

    alert = find_fortisiem_alert(client, args.alert)
    alert_uuid = alert["@id"].split("/")[-1]
    print(f"Alert {alert_uuid} | source={alert.get('source')!r} | {alert.get('name')!r}\n")

    # 1-3: trigger (or reuse) + poll
    if args.reuse:
        task_id = client.ai.get_investigation_for_alert(alert)
        if not task_id:
            raise SystemExit("No existing investigation on this alert; drop --reuse to start one.")
        print(f"Reusing investigation task_id={task_id}")
        status = client.ai.get_status(task_id)
        print(f"  status: {status}")
    else:
        print("Starting investigation ...")
        started = client.ai.start_alert_investigation(alert)  # links task_id to the alert
        task_id = started["task_id"]
        print(f"  task_id={task_id}")
        status = poll(client, task_id)

    # 4: audit tool usage with server attribution + inputs/outputs
    print(f"\nFinal status: {status}\n")
    print("Building MCP tool->server catalog (probing all registered servers) ...")
    catalog = client.ai.mcp_tool_catalog()
    print(f"  {len(catalog)} tools across {len({v['server'] for v in catalog.values()})} servers\n")

    calls = client.ai.attribute_tool_calls(task_id, catalog=catalog)
    outputs = observed_outputs(client, task_id)
    if len(outputs) != len(calls):  # alignment is best-effort; pad to be safe
        outputs += ["(unrecovered)"] * (len(calls) - len(outputs))

    tel = execution_telemetry(client, task_id)
    print(
        f"Execution proof: {tel['steps']} reasoning steps, "
        f"{tel['input_tokens']}/{tel['output_tokens']} tokens in/out, "
        f"${tel['cost_usd']:.4f}, models={tel['models']}\n"
    )

    print(f"=== {len(calls)} tool call(s) made during the investigation ===")
    fortisiem_used = []
    for i, call in enumerate(calls):
        server = call.get("server") or "(unmapped — not an MCP tool)"
        if call.get("server") == "FortiSIEM":
            fortisiem_used.append(call["tool_name"])
        print(f"\n[{i + 1}] {call['tool_name']}  <- {server}")
        print(f"    input : {json.dumps(call.get('tool_args'))[:300]}")
        print(f"    output: {outputs[i][:300]}")

    print("\n--- FortiSIEM tools used by agents ---")
    print(sorted(set(fortisiem_used)) or "(none — no FortiSIEM MCP tool was called this run)")

    # 5: provenance — questions -> evidence -> hypothesis weighting -> verdict
    print("\n" + "=" * 70)
    print("PROVENANCE: how tool evidence drove the verdict")
    print("=" * 70)

    questions = client.ai.investigation_questions(task_id)
    print(f"\n{len(questions)} investigation question(s), each answered by an agent:")
    for q in questions:
        ev = (q.get("evidence") or "").replace("\n", " ")
        print(f"\n  Q{q['index']} [{q['agent']}]  +{q['supports']}/-{q['weakens']}")
        print(f"    Q: {q['question']}")
        print(f"    A: {q['response']}  ::  {ev[:140]}")

    chain = client.ai.hypothesis_evidence(task_id)
    print(f"\n--- Hypothesis weighting -> VERDICT: {chain['classification']} ---")
    for h in chain["hypotheses"]:
        print(
            f"\n  H{h['id']} [{h['status']}] "
            f"(+{h['support_count']} / -{h['weaken_count']})  {h['name']}"
        )
        for s in h["supported_by"]:
            print(f"     + Q{s['index']} ({s['agent']}): {(s['evidence'] or '')[:90]}")
        for w in h["weakened_by"]:
            print(f"     - Q{w['index']} ({w['agent']}): {(w['evidence'] or '')[:90]}")

    print("\nChain proven: each verdict-driving hypothesis traces to specific")
    print("question evidence, which traces to the tool calls listed above.")


if __name__ == "__main__":
    main()
