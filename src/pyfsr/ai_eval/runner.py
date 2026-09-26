"""Deploy the stub MCP servers to an appliance and run scored investigations.

::

    from pyfsr.ai_eval import load_suite, deploy, run_suite

    suite = load_suite()                     # bundled default suite
    dep = deploy(client, suite)              # connector + fixtures + MCP servers + agent allowlists
    report = run_suite(client, suite, runs=3)
    print(report["summary"]["overall"])

Deployment is idempotent: the connector is re-installed in place, the
configuration and MCP servers are upserted by name, and allowlists only gain
missing entries (and lose eval servers an agent is no longer listed for).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..exceptions import APIError
from .scenario import Scenario, Suite
from .score import DEFAULT_PRICING, RunScore, TokenPricing, aggregate, score_run

if TYPE_CHECKING:
    from ..client import FortiSOAR

CONNECTOR = "fsr-ai-eval-stub"
STUB_DIR = Path(__file__).with_name("stub_connector") / CONNECTOR
DEFAULT_PORT = 18900
CONFIG_NAME = "ai-eval"
ALERT_SOURCE = "pyfsr ai_eval"
#: alert fields every FortiSOAR alerts module has; used if the full create is rejected
_BASIC_ALERT_FIELDS = ("name", "description", "severity", "source", "sourceId")


class Deployment(BaseModel):
    port: int
    servers: dict[str, str] = Field(default_factory=dict)  # suite key -> MCP server uuid
    agents: dict[str, list[str]] = Field(default_factory=dict)  # agent name -> suite keys allowed
    fixtures: dict[str, Any] = Field(default_factory=dict)  # load_fixtures result


def _agent_versions(client: FortiSOAR) -> dict[str, str]:
    versions: dict[str, str] = {}
    for agent in client.ai.list_agents():
        name, version = agent.get("name"), agent.get("version")
        if name and version and (agent.get("active") is not False):
            versions.setdefault(name, version)
    return versions


def deploy(
    client: FortiSOAR,
    suite: Suite,
    *,
    port: int = DEFAULT_PORT,
    install: bool = True,
    validate: bool = True,
) -> Deployment:
    """Install the stub connector, load the suite's fixtures, register one MCP
    server per suite server and allow it for the suite's agents.

    Custom connectors need the appliance's connector development mode on
    (``client.system_settings.set_development_mode(connectors=True)``).
    """
    if install:
        client.connectors.install_from_dir(str(STUB_DIR), replace=True, wait=True)
    client.connectors.upsert_configuration(CONNECTOR, {"port": port}, name=CONFIG_NAME, default=True)
    if install:
        # a listener left running by the previous install still runs the old
        # code; load_fixtures below starts the new one
        client.connectors.execute(CONNECTOR, "stop_server", config=CONFIG_NAME)
    loaded = client.connectors.execute(
        CONNECTOR, "load_fixtures", config=CONFIG_NAME, params={"fixtures": suite.fixtures()}
    )
    dep = Deployment(port=port, fixtures=_data(loaded))

    for key, srv in suite.servers.items():
        saved = client.ai.upsert_mcp_server(
            {
                "name": srv.name,
                "description": srv.instructions or srv.name,
                "transport": "http",
                "url": f"http://127.0.0.1:{port}/mcp/{key}",
                "authentication": {"type": "none"},
                "active": True,
            },
            validate=validate,
        )
        dep.servers[key] = saved.uuid

    versions = _agent_versions(client)
    wanted: dict[str, set[str]] = {}
    for key, srv in suite.servers.items():
        for agent in srv.agents:
            wanted.setdefault(agent, set()).add(key)
    eval_uuids = {uuid: key for key, uuid in dep.servers.items()}
    for agent, version in versions.items():
        keys = wanted.get(agent, set())
        try:
            current = set(client.ai.list_agent_mcp_servers(agent, version))
        except APIError:
            current = set()
        for key in keys:
            if dep.servers[key] not in current:
                client.ai.allow_mcp_server_for_agent(agent, version, dep.servers[key])
        for uuid in current & set(eval_uuids):
            if eval_uuids[uuid] not in keys:
                client.ai.disallow_mcp_server_for_agent(agent, version, uuid)
        if keys:
            dep.agents[agent] = sorted(keys)
    missing = sorted(set(wanted) - set(versions))
    if missing:
        dep.agents["(not on this appliance)"] = missing
    return dep


def teardown(client: FortiSOAR, suite: Suite, *, uninstall: bool = False) -> None:
    """Remove the suite's MCP servers from every agent and delete them."""
    names = {srv.name for srv in suite.servers.values()}
    uuids = {m.get("uuid") or m.get("id") for m in client.ai.list_mcp_servers() if m.get("name") in names}
    for agent, version in _agent_versions(client).items():
        try:
            current = set(client.ai.list_agent_mcp_servers(agent, version))
        except APIError:
            continue
        for uuid in current & uuids:
            client.ai.disallow_mcp_server_for_agent(agent, version, uuid)
    for uuid in uuids:
        client.ai.delete_mcp_server(uuid)
    if uninstall:
        client.connectors.execute(CONNECTOR, "stop_server", config=CONFIG_NAME)
        client.connectors.uninstall(CONNECTOR)


def call_log(client: FortiSOAR, *, since: float | None = None, clear: bool = False) -> list[dict[str, Any]]:
    """Every tool call the stub servers answered (independent of FortiSOAR's traces)."""
    resp = client.connectors.execute(
        CONNECTOR, "get_call_log", config=CONFIG_NAME, params={"since": since or 0, "clear": clear}
    )
    return (_data(resp) or {}).get("calls", [])


def _data(resp: Any) -> Any:
    data = getattr(resp, "data", None)
    if data is None and isinstance(resp, dict):
        data = resp.get("data", resp)
    return data


def create_alert(client: FortiSOAR, scenario: Scenario, *, tag: str | None = None) -> dict[str, Any]:
    """Create the scenario's alert (a fresh ``sourceId`` each time)."""
    fields = dict(scenario.alert)
    fields.setdefault("source", ALERT_SOURCE)
    fields["sourceId"] = f"ai-eval-{scenario.id}-{tag or int(time.time())}"
    try:
        return client.alerts.create(**fields)
    except APIError:
        return client.alerts.create(**{k: v for k, v in fields.items() if k in _BASIC_ALERT_FIELDS})


def _trace(client: FortiSOAR, task_id: str, *, attempts: int = 4, delay: float = 5.0):
    """The investigation trace; the tracer store can lag the result by a few seconds."""
    inv = None
    for _ in range(attempts):
        try:
            inv = client.ai.traces.investigation(task_id)
        except APIError:
            inv = None
        if inv is not None and inv.questions:
            return inv
        time.sleep(delay)
    return inv


def _balance(client: FortiSOAR) -> int | None:
    """Tokens left in the FortiAI pool, or None when it can't be read (native LLM profile, no connector)."""
    try:
        return client.ai.token_balance().remaining
    except (APIError, ValueError):
        return None


def run_scenario(
    client: FortiSOAR,
    suite: Suite,
    scenario: Scenario | str,
    *,
    runs: int = 1,
    alert: dict[str, Any] | None = None,
    interval: float = 6.0,
    timeout: float = 1200.0,
    on_run: Any = None,
    meter: bool = True,
    pricing: TokenPricing = DEFAULT_PRICING,
) -> list[RunScore]:
    """Investigate one scenario's alert ``runs`` times and score each run.

    The same alert is re-investigated each time (created once unless ``alert``
    is given), so the spread across runs is the investigation's own variance.
    ``on_run(score)`` is called after each run (progress printing).

    With ``meter`` the FortiAI token balance is read before and after each run;
    the drop is the run's ``metered_tokens``. Anything else using FortiAI on the
    appliance at the same time (chat, other investigations) inflates it.
    """
    sc = suite.scenario(scenario) if isinstance(scenario, str) else scenario
    alert = alert or create_alert(client, sc)
    scores: list[RunScore] = []
    for _ in range(runs):
        started = time.monotonic()
        before = _balance(client) if meter else None
        try:
            handle = client.ai.start_alert_investigation(alert)
            result = client.ai.wait_for_result(handle.task_id, interval=interval, timeout=timeout)
            summary = result.summary if isinstance(result.summary, dict) else {}
            inv = _trace(client, handle.task_id)
            after = _balance(client) if before is not None else None
            score = score_run(
                suite,
                sc,
                inv,
                summary=summary,
                classification=summary.get("classification"),
                duration_s=round(time.monotonic() - started, 1),
                metered_tokens=before - after if after is not None else None,
                pricing=pricing,
            )
            score.task_id = handle.task_id
            score.status = result.status or score.status
        except APIError as exc:
            score = RunScore(scenario=sc.id, error=str(exc), duration_s=round(time.monotonic() - started, 1))
        scores.append(score)
        if on_run:
            on_run(score)
    return scores


def run_suite(
    client: FortiSOAR,
    suite: Suite,
    *,
    runs: int = 1,
    scenarios: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run every (or the named) scenario ``runs`` times.

    Returns ``{"summary": aggregate(...), "runs": [RunScore dicts]}``.
    """
    chosen = [suite.scenario(s) for s in scenarios] if scenarios else suite.scenarios
    pricing = kwargs.get("pricing", DEFAULT_PRICING)
    scores: list[RunScore] = []
    for sc in chosen:
        scores.extend(run_scenario(client, suite, sc, runs=runs, **kwargs))
    return {"summary": aggregate(scores, pricing), "runs": [s.model_dump() for s in scores]}
