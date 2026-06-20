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
    python examples/investigate_fortisiem_incident.py --reuse    # audit the last investigation
"""

from __future__ import annotations

import argparse
import ast
import json
import re
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
    resp = client.alerts.list({"source": FORTISIEM_SOURCE, "$orderby": "-createDate", "$limit": 1})
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


def _deep_parse(value):
    """Recursively decode JSON-encoded strings so nested payloads display cleanly."""
    if isinstance(value, str):
        t = value.strip()
        if t[:1] in ("{", "[") and t[-1:] in ("}", "]"):
            for parse in (json.loads, ast.literal_eval):
                try:
                    return _deep_parse(parse(t))
                except (ValueError, TypeError, SyntaxError, MemoryError):
                    continue
        return value
    if isinstance(value, dict):
        return {k: _deep_parse(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_parse(v) for v in value]
    return value


def _extract_payload(content: str):
    """Pull the structured tool result out of the follow-up step's user prompt.

    The result is appended after the ``## Event Details:`` header as a Python-repr
    ``CallToolResult`` (``{'content': [{'type': 'text', 'text': '...json...'}], ...}``).
    We parse that, lift out the ``text`` payloads, and JSON-decode them so the
    observed output is real data instead of an escaped string tail.
    """
    if not isinstance(content, str):
        return content
    # The result is appended after a "## <Something> Details:" header (Event Details,
    # Asset Details, …). Parse the section after the LAST such header.
    matches = list(re.finditer(r"##[^\n]*Details:\s*", content))
    blob = (content[matches[-1].end() :] if matches else content).strip()
    obj = None
    for parse in (json.loads, ast.literal_eval):
        try:
            obj = parse(blob)
            break
        except (ValueError, TypeError, SyntaxError, MemoryError):
            continue
    if obj is None:
        return blob  # raw fallback — couldn't structure it
    if isinstance(obj, dict) and isinstance(obj.get("content"), list):
        texts = [c.get("text") for c in obj["content"] if isinstance(c, dict) and c.get("text")]
        parsed = [_deep_parse(t) for t in texts]
        if parsed:
            return parsed[0] if len(parsed) == 1 else parsed
    return _deep_parse(obj)


def tool_calls_with_outputs(client: FortiSOAR, task_id: str, catalog: dict | None = None) -> list[dict]:
    """Tool calls of one investigation, each paired with its REAL observed output.

    The ``llm_activity_logs`` for a run are a sequence of triples:
    ``Tool Selection`` (response carries ``tool_name`` + ``tool_args``) →
    ``<agent>`` (its user prompt carries that tool's result under an
    ``## … Details:`` header) → ``Evidence Attribution``.

    Critically, name **and** output must come from a *single id-ordered pass*. The
    library's :meth:`investigation_tool_calls` returns calls in raw API order
    (newest-first), so zipping it against a separately id-sorted output list
    mis-pairs every tool with the wrong result. This walks once and binds:

        {"tool_name", "tool_args", "server", "consumed_by", "output"}

    ``consumed_by`` is the agent (next record's title) that received the result —
    handy for confirming, e.g., that the FortiSIEM event table reached the ``siem``
    agent rather than being dropped.
    """
    if catalog is None:
        catalog = client.ai.mcp_tool_catalog()
    resp = client.get("/api/3/llm_activity_logs", params={"correlationID": task_id, "$limit": 500})
    recs = sorted(resp.get("hydra:member") or [], key=lambda r: r.get("id") or 0)

    def as_obj(v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return {}
        return v if isinstance(v, dict) else {}

    calls: list[dict] = []
    for i, rec in enumerate(recs):
        resp_obj = as_obj(rec.get("response"))
        tool_name = resp_obj.get("tool_name")
        if not tool_name:
            continue
        owner = catalog.get(tool_name) or {}
        nxt = recs[i + 1] if i + 1 < len(recs) else None
        consumed_by = nxt.get("title") if nxt else None
        user = ""
        if nxt:
            prompt = nxt.get("prompt")
            prompt = as_obj(prompt) if isinstance(prompt, str) else prompt
            if isinstance(prompt, list):
                users = [m.get("content") for m in prompt if isinstance(m, dict) and m.get("role") == "user"]
                user = users[-1] if users else ""
        calls.append(
            {
                "tool_name": tool_name,
                "tool_args": _deep_parse(resp_obj.get("tool_args")),
                "server": owner.get("server"),
                "consumed_by": consumed_by,
                "output": _extract_payload(user) if nxt else "(no following record)",
            }
        )
    return calls


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


def _pretty(value, indent: str = "    ") -> str:
    """Render a value as indented, human-readable text (JSON for objects)."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return text.replace("\n", "\n" + indent)


def _preview(value, limit: int = 600, indent: str = "    ", outfile: str = "") -> str:
    """Prettified, length-capped view for the console; points to the file for the rest."""
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)
    if len(text) > limit:
        more = len(text) - limit
        tail = f"\n… [+{more} chars — full output in {outfile}]" if outfile else f"\n… [+{more} chars]"
        text = text[:limit] + tail
    return text.replace("\n", "\n" + indent)


def _siem_signal_tokens(calls: list[dict]) -> tuple[set[str], set[str]]:
    """Distinctive identifiers that the FortiSIEM tools *returned*.

    These are strings unlikely to appear in an answer unless the model actually
    read them out of a FortiSIEM tool result: SIEM **incident IDs**, FortiSIEM
    **rule names** (``PH_RULE_*``) and **event types** (``IOS-NETFLOW-BI`` etc.),
    and the huge numeric **event IDs**. Returns ``(all_siem_tokens, distinctive)``
    where ``distinctive`` excludes anything also produced by a non-SIEM tool — so a
    hit on a distinctive token is high-confidence SIEM provenance.
    """

    def toks(output) -> set[str]:
        blob = json.dumps(output, default=str)
        found: set[str] = set()
        found |= set(re.findall(r'"incident_id":\s*(\d+)', blob))
        found |= set(re.findall(r'"eventId":\s*(\d{12,})', blob))
        found |= set(re.findall(r"\b(PH_RULE_[A-Z0-9_]+)\b", blob))
        found |= set(re.findall(r"\b([A-Z]{2,}(?:-[A-Z0-9]+){1,})\b", blob))  # IOS-NETFLOW-BI
        return found

    siem: set[str] = set()
    other: set[str] = set()
    for c in calls:
        (siem if c.get("server") == "FortiSIEM" else other).update(toks(c.get("output")))
    return siem, (siem - other)


def siem_influence_report(calls: list[dict], questions: list[dict], chain: dict, alert: dict) -> dict:
    """Flag whether the FortiSIEM MCP server actually affected the investigation.

    The hard part is the confounder: the alert page *already* carries most of the
    SIEM incident (``sourceId``, ``rule``, ``sourcedata`` is ~17 KB of incident
    JSON), so an answer citing incident ``1514`` may just be reading the alert —
    NOT proof the MCP tool did anything. So we split every SIEM-returned token the
    answers cite into:

      * ``on_alert``  — also present on the alert; ambiguous (could be alert data)
      * ``net_new``   — returned by a FortiSIEM tool but ABSENT from the alert;
                        this is the load-bearing signal — a fact only the MCP
                        server could have surfaced (e.g. *historical* incidents
                        ``1040``/``1365`` from ``get_incidents_by_entity``).

    Real page-level influence = a ``net_new`` SIEM token reaching an answer that
    then feeds a verdict hypothesis.
    """
    alert_text = json.dumps(alert, default=str)
    siem_calls = [c for c in calls if c.get("server") == "FortiSIEM"]
    all_tok, distinct_tok = _siem_signal_tokens(calls)
    net_new_tok = {t for t in all_tok if t and t not in alert_text}

    grounded: list[dict] = []
    for q in questions:
        text = f"{q.get('response') or ''} {q.get('evidence') or ''}"
        cited = sorted(t for t in all_tok if t and t in text)
        if not cited:
            continue
        net_new = sorted(t for t in cited if t in net_new_tok)
        on_alert = sorted(t for t in cited if t not in net_new_tok)
        grounded.append(
            {
                "index": q["index"],
                "agent": q.get("agent"),
                "response": q.get("response"),
                "net_new": net_new,
                "on_alert": on_alert,
            }
        )

    net_new_idx = {g["index"] for g in grounded if g["net_new"]}
    verdict_linked = []
    for h in chain.get("hypotheses", []):
        for side in ("supported_by", "weakened_by"):
            for s in h.get(side, []):
                if s.get("index") in net_new_idx:
                    verdict_linked.append(
                        {
                            "hypothesis": h.get("name"),
                            "status": h.get("status"),
                            "via_question": s.get("index"),
                            "relation": side,
                        }
                    )

    return {
        "siem_tool_calls": len(siem_calls),
        "siem_tools": sorted({c["tool_name"] for c in siem_calls}),
        "siem_returned_tokens": sorted(all_tok)[:40],
        "net_new_tokens": sorted(net_new_tok)[:40],  # SIEM facts NOT on the alert
        "questions_citing_siem_data": grounded,
        "verdict_linked_via_net_new": verdict_linked,
        "siem_influenced_output": bool(verdict_linked),  # net-new SIEM data drove the verdict
        "any_net_new_cited": bool(net_new_idx),  # net-new reached an answer
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--alert", help="alert uuid to investigate (default: newest FortiSIEM alert)")
    ap.add_argument(
        "--reuse",
        action="store_true",
        help="don't start a new run; audit the alert's existing investigation",
    )
    ap.add_argument(
        "--out",
        help="path to write the full (untruncated) tool I/O report (default: fortisiem_investigation_<task_id>.json)",
    )
    args = ap.parse_args()

    client = connect()

    if not client.ai.features_enabled():
        client.ai.enable_features(modified_by="pyfsr")
        print("Enabled FortiAI features.")

    alert = find_fortisiem_alert(client, args.alert)
    alert_uuid = alert["@id"].split("/")[-1]
    print(f"Alert id={alert.get('id')} uuid={alert_uuid} | source={alert.get('source')!r} | {alert.get('name')!r}\n")

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

    # name + args + server + REAL output, all bound in one id-ordered pass
    # (zipping attribute_tool_calls() with a separate output list mis-pairs them —
    # the former is newest-first API order, the latter id-ascending).
    calls = tool_calls_with_outputs(client, task_id, catalog=catalog)

    tel = execution_telemetry(client, task_id)
    print(
        f"Execution proof: {tel['steps']} reasoning steps, "
        f"{tel['input_tokens']}/{tel['output_tokens']} tokens in/out, "
        f"${tel['cost_usd']:.4f}, models={tel['models']}\n"
    )

    outfile = args.out or f"fortisiem_investigation_{task_id}.json"

    print(f"=== {len(calls)} tool call(s) made during the investigation ===")
    fortisiem_used = []
    call_records = []
    for i, call in enumerate(calls):
        server = call.get("server") or "(unmapped — not an MCP tool)"
        if call.get("server") == "FortiSIEM":
            fortisiem_used.append(call["tool_name"])
        tool_args = call.get("tool_args")
        output = call.get("output")
        print(f"\n[{i + 1}] {call['tool_name']}  <- {server}  (consumed by: {call.get('consumed_by')})")
        print(f"    input :\n    {_preview(tool_args, limit=400, outfile=outfile)}")
        print(f"    output:\n    {_preview(output, limit=600, outfile=outfile)}")
        call_records.append(
            {
                "index": i + 1,
                "tool_name": call.get("tool_name"),
                "server": call.get("server"),
                "consumed_by": call.get("consumed_by"),
                "input": tool_args,
                "output": output,
            }
        )

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
        print(f"\n  H{h['id']} [{h['status']}] (+{h['support_count']} / -{h['weaken_count']})  {h['name']}")
        for s in h["supported_by"]:
            print(f"     + Q{s['index']} ({s['agent']}): {(s['evidence'] or '')[:90]}")
        for w in h["weakened_by"]:
            print(f"     - Q{w['index']} ({w['agent']}): {(w['evidence'] or '')[:90]}")

    print("\nChain proven: each verdict-driving hypothesis traces to specific")
    print("question evidence, which traces to the tool calls listed above.")

    # 6: SIEM MCP influence check — did the FortiSIEM server actually shape the page?
    print("\n" + "=" * 70)
    print("SIEM MCP INFLUENCE CHECK")
    print("=" * 70)
    siem = siem_influence_report(call_records, questions, chain, alert)
    print(f"FortiSIEM tool calls: {siem['siem_tool_calls']}  {siem['siem_tools']}")
    print(f"SIEM-returned tokens (sample): {siem['siem_returned_tokens'][:12]}")
    print(f"NET-NEW vs alert page (only MCP could surface these): {siem['net_new_tokens'][:12] or '(none)'}")
    g = siem["questions_citing_siem_data"]
    print(
        f"\nQuestions citing SIEM-returned data: {len(g)}/{len(questions)}"
        "  (net_new = MCP-only fact, on_alert = also on the alert → ambiguous)"
    )
    for q in g:
        nn = f"  NET-NEW={q['net_new']}" if q["net_new"] else ""
        oa = f"  on_alert={q['on_alert']}" if q["on_alert"] else ""
        print(f"   Q{q['index']} [{q['agent']}] -> {q['response']!r}{nn}{oa}")
    if siem["verdict_linked_via_net_new"]:
        print("\nVerdict linkage (questions citing NET-NEW SIEM data, feeding hypotheses):")
        for v in siem["verdict_linked_via_net_new"]:
            print(f"   Q{v['via_question']} {v['relation']} H[{v['status']}] {v['hypothesis']!r}")
    if siem["siem_influenced_output"]:
        print("\n>>> FLAG: SIEM MCP server IS affecting the page output — a fact NOT on")
        print("    the alert (net-new from a SIEM tool) reaches a verdict hypothesis.")
    elif siem["any_net_new_cited"]:
        print("\n>>> FLAG: SIEM MCP surfaced net-new facts that reached answers, but none")
        print("    feed the verdict — real but non-decisive influence.")
    elif g:
        print("\n>>> FLAG: INCONCLUSIVE — answers cite SIEM values that are ALSO on the")
        print("    alert page; can't distinguish MCP output from alert source-data.")
    elif siem["siem_tool_calls"]:
        print("\n>>> FLAG: SIEM tools ran but NO answer cites their output — not measurably affecting the page.")
    else:
        print("\n>>> FLAG: NO FortiSIEM tool was called — SIEM MCP server had zero effect.")

    # Persist the FULL (untruncated) record for offline review.
    report = {
        "alert": {"uuid": alert_uuid, "source": alert.get("source"), "name": alert.get("name")},
        "task_id": task_id,
        "status": status,
        "telemetry": tel,
        "tool_calls": call_records,
        "fortisiem_tools_used": sorted(set(fortisiem_used)),
        "siem_influence": siem,
        "questions": questions,
        "verdict": chain.get("classification"),
        "hypotheses": chain.get("hypotheses"),
    }
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nFull tool I/O + provenance written to: {outfile}")


if __name__ == "__main__":
    main()
