"""Trigger a FortiAI agentic investigation on an alert and print the verdict.

Prereqs on the FortiSOAR side (all doable via this client):
  * AI features enabled / terms accepted  -> client.ai.enable_features()
  * At least one LLM reasoning profile     -> client.ai.list_llm_configs()
  * The FortiAI solution pack installed     -> client.ai.list_providers()
"""

from pyfsr import FortiSOAR, Query

client = FortiSOAR.from_config_file("config.toml", suppress_insecure_warnings=True)

# One-time: make sure FortiAI is turned on (accepts the AI terms & conditions).
if not client.ai.features_enabled():
    client.ai.enable_features(modified_by="pyfsr")
    print("Enabled FortiAI features.")

print("Providers:   ", [p.get("label") for p in client.ai.list_providers()])
print("Reasoning:   ", [c.get("name") for c in client.ai.list_llm_configs()])
print("MCP servers: ", [m.get("name") for m in client.ai.list_mcp_servers()])

# Pick the newest alert and investigate it, blocking until a verdict. The modern
# records() surface returns a typed (dict-compatible) Alert and unpacks the hydra
# envelope for us — no ["hydra:member"][0] indexing.
alert = client.records("alerts").first(Query().sort("createDate", "DESC"))
if alert is None:
    raise SystemExit("No alerts found to investigate.")
print(f"\nInvestigating alert {alert['@id']} — {alert.get('name')!r} ...")

report = client.ai.investigate_alert(alert, wait=True, timeout=600)
print("Status:", report.get("status"))
for phase in report.get("phases", []):
    print(f"  [{phase.get('status'):9}] {phase.get('state')}")
if report.get("error"):
    print("Error:", report["error"])
