"""Run N investigations on one alert and report how often the FortiSIEM MCP
server measurably influenced the verdict. Reuses investigate_fortisiem_incident.py.

Usage: python3 siem_influence_trials.py <alert_uuid> <count> <label>
"""

import importlib.util
import sys

import tomllib

from pyfsr import FortiSOAR

spec = importlib.util.spec_from_file_location("inv", "investigate_fortisiem_incident.py")
inv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inv)

alert_uuid, count, label = sys.argv[1], int(sys.argv[2]), sys.argv[3]
cfg = tomllib.load(open("config.toml", "rb"))["fortisoar"]
c = FortiSOAR(
    base_url=cfg["base_url"],
    auth=(cfg["auth"]["username"], cfg["auth"]["password"]),
    verify_ssl=False,
    suppress_insecure_warnings=True,
)

catalog = c.ai.mcp_tool_catalog()
alert = c.alerts.get(alert_uuid)
results = []
for i in range(1, count + 1):
    started = c.ai.start_alert_investigation(alert)
    tid = started["task_id"]
    status = inv.poll(c, tid, interval=6, timeout=600)
    calls = inv.tool_calls_with_outputs(c, tid, catalog=catalog)
    qs = c.ai.investigation_questions(tid)
    chain = c.ai.hypothesis_evidence(tid)
    rep = inv.siem_influence_report(calls, qs, chain, alert)
    flag = (
        "INFLUENCED"
        if rep["siem_influenced_output"]
        else "net-new-unused"
        if rep["any_net_new_cited"]
        else "inconclusive"
        if rep["questions_citing_siem_data"]
        else "no-cite"
        if rep["siem_tool_calls"]
        else "no-siem-call"
    )
    results.append(flag)
    print(
        f"[{label}] trial {i}/{count} task={tid[:8]} status={status} "
        f"verdict={chain.get('classification')!r} "
        f"siem_calls={rep['siem_tool_calls']} net_new={rep['net_new_tokens'][:6]} -> {flag}",
        flush=True,
    )

infl = results.count("INFLUENCED")
breakdown = {r: results.count(r) for r in set(results)}
print(f"[{label}] SUMMARY: {infl}/{count} runs INFLUENCED | breakdown={breakdown}", flush=True)
