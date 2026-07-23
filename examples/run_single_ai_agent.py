"""Run ONE FortiAI agent directly and print its answer.

The companion to `trigger_ai_investigation.py`. That script runs the whole
investigation pipeline on an alert (hypothesis -> planning -> evidence ->
verdict); this one calls a single agent to answer a single question, which is
far cheaper and is what you want when you just need an enrichment, a lookup, or
a natural-language query answered.

Every installed agent publishes its own input contract as `inputformat`, so you
never have to guess the payload — `client.ai.agent_input_schema(name)` returns
it, and `run_agent(..., validate=True)` (the default) checks your payload
against it BEFORE spending an LLM call.

Prereqs on the FortiSOAR side:
  * AI features enabled / terms accepted -> client.ai.enable_features()
  * The FortiAI solution pack installed, with agents active
  * The calling role needs `execute.ai_agents` (plus `read.ai_agents` to read
    the input schema); without it the trigger returns a bare "Access Denied".
"""

from pyfsr import FortiSOAR

client = FortiSOAR.from_config_file("config.toml", suppress_insecure_warnings=True)

if not client.ai.features_enabled():
    client.ai.enable_features(modified_by="pyfsr")
    print("Enabled FortiAI features.")

# What can we call? Each agent's `label` is the human name shown in the UI.
agents = [a for a in client.ai.list_agents() if a.active]
print(f"{len(agents)} active agents:")
for a in agents[:10]:
    print(f"  {a.name:26} {a.label}")

# Every agent declares the exact keys it expects, with required flags, enums and
# examples. Read it rather than guessing — the shape differs per agent:
#   ioc-enrichment      -> {"question": str, "ioc": [{"type", "value"}]}
#   alert-investigation -> {"data": <raw alert>}
AGENT = "ioc-enrichment"
schema = client.ai.agent_input_schema(AGENT)
print(f"\n{AGENT} expects:")
for key, spec in schema.items():
    required = "required" if isinstance(spec, dict) and spec.get("required") else "optional"
    described = spec.get("description", "") if isinstance(spec, dict) else str(spec)
    print(f"  {key:12} ({required}) {described[:70]}")

# Run it. wait=True polls to a terminal status and returns the typed result;
# drop it to get a {"task_id", "status"} handle back immediately instead.
result = client.ai.run_agent(
    AGENT,
    {
        "question": "Is this IP address known to be malicious?",
        "ioc": [{"type": "IP Address", "value": "8.8.8.8"}],
    },
    wait=True,
    timeout=300,
)

# A single agent answers one question, so the result carries the agent's own
# outputformat (answer/evidence/confidence) rather than an investigation's
# summary/hypotheses. `phases` stays empty here - it is only filled in by the
# full pipeline.
print(f"\nStatus:     {result.status}")
print(f"Answer:     {result.answer}")
print(f"Confidence: {result.confidence}")
print(f"Evidence:   {result.evidence}")

# On timeout the latest result comes back with a non-terminal status rather than
# raising, so check before trusting the answer.
if result.status not in ("completed", "failed", "error", "cancelled"):
    print(f"\nNOTE: still {result.status} at timeout - poll again with client.ai.get_agent_result({result.task_id!r})")

# Asking for something the agent requires but you did not supply fails fast,
# locally, naming the missing key - no LLM call is made.
try:
    client.ai.run_agent(AGENT, {"question": "no indicator supplied"})
except ValueError as exc:
    print(f"\nValidation caught it before the API call:\n  {exc}")
