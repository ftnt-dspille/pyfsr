"""Discover the real inputformat of every FortiAI agent on the box.

The documented inputformat (GET /api/ai/agent/{name}/{version}) is the
primary discovery source, but it is sometimes INCOMPLETE:

  - task-planner requires `chain_of_thought` (not marked required)
  - conversation requires `user_id` (not in the inputformat at all)

This script combines two signals:

  1. DOCUMENTED: client.ai.agent_input_schema(name) — the inputformat field
     from the agent definition. Lists keys, types, required flags.
  2. ACTUAL: send an empty payload to the trigger endpoint and read the
     validation error. The error names the REAL required keys, catching
     undocumented ones.

Usage:
    python examples/probe_fortiai_agents.py [--run]

    --run    also send a minimal sample payload to each agent and print the
             result shape (verdict vs. structured). Omit for a fast
             schema-only scan.

Requires: FortiAI features enabled + execute.ai_agents role.
"""

from __future__ import annotations

import argparse
import time

from pyfsr import FortiSOAR

client = FortiSOAR.from_config_file("config.toml", suppress_insecure_warnings=True)

if not client.ai.features_enabled():
    client.ai.enable_features(modified_by="pyfsr")
    print("Enabled FortiAI features.\n")


def discover_inputformat(name: str) -> dict:
    """Return {documented: [...], actual: [...], hidden: [...]} for one agent.

    documented = keys in the inputformat.
    actual     = keys the trigger endpoint's validation error names as required.
    hidden     = actual keys NOT in the documented inputformat.
    """
    # 1. Documented: the inputformat field on the agent definition.
    try:
        schema = client.ai.agent_input_schema(name)
        documented = list(schema.keys())
    except Exception:
        documented = []
        schema = {}

    # 2. Actual: send an empty payload, read the validation error.
    # The error surfaces as a `failed` status in the result, not a raised
    # exception — so we trigger, poll, and read the result's error field.
    actual: list[str] = []
    try:
        trig = client.post(f"/api/ai/agents/{name}/trigger", data={})
        task_id = trig.get("task_id") if isinstance(trig, dict) else None
        if task_id:
            time.sleep(6)  # let the validation error settle
            res = client.get(f"/api/ai/agents/{task_id}/result")
            err = res.get("error") if isinstance(res, dict) else None
            if err:
                # Parse the error for field names. Two patterns:
                #   pydantic: "Field required ... input_value={'...': ...}"
                #   custom: "Query should not be empty" / "'NoneType' object..."
                if "Field required" in err:
                    # pydantic lists each missing field above its error block
                    for line in err.split("\n"):
                        line = line.strip()
                        if line and "Field required" not in line and "For further" not in line:
                            if not line.startswith("input_value") and not line.startswith("input_type"):
                                actual.append(line)
                elif "should not be empty" in err:
                    # e.g. "Query should not be empty" → the field is "query"
                    actual.append(err.split("should not be empty")[0].strip().lower())
                # Fall through: error doesn't name a field (e.g. "'NoneType'...")
    except Exception:
        pass

    hidden = [k for k in actual if k not in documented]
    return {
        "documented": documented,
        "actual": actual,
        "hidden": hidden,
        "schema": schema,
    }


def run_sample(name: str) -> None:
    """Send a minimal sample payload and print the result shape."""
    # Minimal payloads for agents we can satisfy without a live record context.
    samples = {
        "ioc-enrichment": {"question": "Is 8.8.8.8 malicious?", "ioc": [{"type": "IP Address", "value": "8.8.8.8"}]},
        "conversation": {"messages": ["hello"], "user_id": "analyst@example.com"},
        "organization-context": {},
        "metric-computation": {"data": [{"x": 1}], "natural_language_task": "count items"},
        "summary": {"data": [{"step": "test", "verdict": "benign"}], "natural_language_task": "summarize"},
        "task-planner": {
            "query": "investigate alert",
            "socrole": "L1",
            "generate_plan": "true",
            "chain_of_thought": "decompose",
        },
        "risk-evaluation": {"data": [{"type": "alert", "severity": "High"}], "natural_language_task": "evaluate risk"},
    }
    payload = samples.get(name)
    if payload is None:
        print("    (no sample payload — skipping live run)")
        return
    try:
        result = client.ai.run_agent(name, payload, wait=True, timeout=90)
    except Exception as e:
        print(f"    run error: {type(e).__name__}: {str(e)[:200]}")
        return
    family = "verdict" if result.answer else "structured" if getattr(result, "data", None) else "unknown"
    print(f"    status={result.status}  family={family}")
    if result.answer:
        print(f"    answer={str(result.answer)[:100]!r}  confidence={result.confidence!r}")
    raw = result.model_dump() if hasattr(result, "model_dump") else vars(result)
    data = raw.get("data")
    if data:
        print(f"    data={str(data)[:200]!r}")


def main():
    parser = argparse.ArgumentParser(description="Discover FortiAI agent inputformats")
    parser.add_argument("--run", action="store_true", help="also run a sample payload per agent")
    args = parser.parse_args()

    agents = client.ai.list_agents()
    print(f"{len(agents)} agents on box:\n")

    for agent in agents:
        name = agent.name
        print(f"  {name}")
        print(f"    label: {agent.label}")
        print(f"    active: {agent.active}")
        info = discover_inputformat(name)
        print(f"    documented inputformat: {info['documented']}")
        if info["hidden"]:
            print(f"    HIDDEN required keys:  {info['hidden']}  ← not in inputformat!")
        if info["actual"]:
            print(f"    actual required keys:   {info['actual']}")
        # Print the schema detail for the first few keys
        for k, spec in list(info["schema"].items())[:5]:
            if isinstance(spec, dict):
                req = "REQUIRED" if spec.get("required") else "optional"
                t = spec.get("type", "?")
                desc = (spec.get("description") or "")[:60]
                print(f"      {k:20} {req:8} {t:10} {desc}")
        if args.run:
            run_sample(name)
        print()


if __name__ == "__main__":
    main()
