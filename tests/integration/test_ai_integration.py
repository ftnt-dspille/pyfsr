"""Live integration tests for the FortiAI / agentic-AI API (opt-in: pytest -m integration).

Every test here is free to run: none starts an investigation, a chat or any
other agent work, so none spends LLM tokens. They read agents, reasoning
profiles, MCP servers, Organization Context and existing traces. The writes put
things back in a ``finally``: ``test_assign_llm_roundtrip`` moves one agent to
another profile, and ``test_install_and_run_custom_agent`` installs a no-LLM
echo agent (``pyfsr-it-echo``), runs it, then deactivates it.

Needs FortiSOAR 8.0+ with AI features enabled; trace tests need 8.0.1+ and
skip on a box that has no traces yet.
"""

import base64
import json
import zipfile

import pytest

from pyfsr.exceptions import APIError

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ai(client):
    """The AI API, or skip when the appliance has no enabled AI stack."""
    try:
        enabled = client.ai.features_enabled()
    except APIError as exc:
        pytest.skip(f"cannot read AI feature flag: {exc}")
    if not enabled:
        pytest.skip("AI features are not enabled on this appliance")
    return client.ai


@pytest.fixture(scope="module")
def agents(ai):
    found = ai.list_agents()
    assert found, "an AI-enabled appliance ships built-in agents"
    return found


@pytest.fixture(scope="module")
def traces(ai):
    try:
        healthy = ai.traces.health()
    except APIError as exc:
        pytest.skip(f"no tracer store (8.0.1+ only): {exc}")
    assert healthy, "tracer store reports unhealthy"
    return ai.traces


# ------------------------------------------------------------------- agents
def test_list_agents(agents):
    names = [a.name for a in agents]
    assert all(names), "every agent has a name"
    assert len(names) == len(set(names)), f"duplicate agent names: {names}"
    assert all(a.version for a in agents), "every agent has a version"
    assert "alert-investigation" in names


def test_investigation_planner_candidates_are_tagged(agents):
    """The investigation planner only routes questions to agents tagged ``Triage``."""
    triage = [a.name for a in agents if "Triage" in (a.tags or [])]
    assert triage, "no Triage-tagged agents: the investigation planner would have nobody to ask"


def test_get_agent_config_every_agent(ai, agents):
    """Covers agents with no config row, which fsr-ai answers with a 500."""
    for agent in agents:
        cfg = ai.get_agent_config(agent.name, agent.version)
        assert cfg.agent_name in (None, agent.name), f"{agent.name}: config for {cfg.agent_name}"


def test_export_and_validate_agent_package(ai, agents, tmp_path):
    name = "siem" if any(a.name == "siem" for a in agents) else agents[0].name
    archive = tmp_path / f"{name}.zip"
    assert ai.export_agent(name, str(archive)) == str(archive)
    assert zipfile.is_zipfile(archive), "export did not return a zip archive"
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(tmp_path / "pkg")
    roots = [p for p in (tmp_path / "pkg").iterdir() if (p / "info.json").exists()]
    source = roots[0] if roots else tmp_path / "pkg"
    package = ai.validate_agent_package(str(source))
    assert package.info.name == name


# 1x1 transparent PNG, for the manifest icons
_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")

_ECHO_AGENT_PY = """\
from agents.base_agent import BaseAgent


class PyfsrItEchoAgent(BaseAgent):
    def act(self, input_data):
        self.initialize()
        return {"answer": "echo: " + input_data.get("question", ""), "agent": "pyfsr-it-echo"}
"""


def _echo_agent_dir(tmp_path):
    root = tmp_path / "pyfsr-it-echo"
    (root / "config").mkdir(parents=True)
    (root / "images").mkdir()
    info = {
        "name": "pyfsr-it-echo",
        "label": "pyfsr integration echo",
        "agentclass": "PyfsrItEchoAgent",
        "version": "1.0.0",
        "description": "pyfsr integration test agent: echoes its input, no LLM call.",
        "publisher": "pyfsr tests",
        "category": "Custom",
        "icon_small_name": "small.png",
        "icon_large_name": "large.png",
        "tags": ["Test"],
        "fsrMinCompatibility": "8.0.1",
        "inputformat": {"question": {"type": "string", "required": True}},
    }
    (root / "info.json").write_text(json.dumps(info))
    (root / "prompt.yaml").write_text("{}\n")
    (root / "config" / "memory.yaml").write_text("allowed_tools: {}\n")
    (root / "agent.py").write_text(_ECHO_AGENT_PY)
    (root / "__init__.py").write_text("")
    for icon in ("small.png", "large.png"):
        (root / "images" / icon).write_bytes(_PNG)
    return root


def test_install_and_run_custom_agent(client, ai, tmp_path):
    """Upload -> activate -> trigger a custom agent; the agent makes no LLM call."""
    was_allowed = client.system_settings.agent_upload_allowed()
    agent = None
    try:
        agent = ai.install_agent(str(_echo_agent_dir(tmp_path)))
        assert agent.name == "pyfsr-it-echo"
        assert agent.active
        assert client.system_settings.agent_upload_allowed()
        assert [a.name for a in ai.list_agents()].count("pyfsr-it-echo") == 1, "replace left a duplicate row"
        result = ai.run_agent("pyfsr-it-echo", {"question": "ping"}, wait=True, interval=2, timeout=120)
        assert result.status == "completed", result.error
        assert result.answer == "echo: ping"
    finally:
        if agent is not None:
            ai.activate_agent([agent.uuid], active=False)
        if not was_allowed:
            client.system_settings.allow_agent_upload(False)


# ---------------------------------------------------------------- LLM routing
def test_llm_profiles_and_assignments(ai, agents):
    profiles = ai.list_llm_configs()
    assert profiles, "no reasoning profiles"
    uuids = {p.uuid for p in profiles}
    names = {p.name for p in profiles}
    assert all(p.uuid and p.name for p in profiles)

    assignments = ai.llm_assignments()
    assert {a.name for a in agents} <= set(assignments), "llm_assignments() misses agents"
    for agent, row in assignments.items():
        profile = row.get("llm_provider")
        if profile:
            assert profile in uuids or profile in names, f"{agent} points at unknown profile {profile!r}"


def test_assign_llm_roundtrip(ai):
    """Move one agent to another profile and back. No LLM call is made."""
    profiles = ai.list_llm_configs()
    if len(profiles) < 2:
        pytest.skip("needs two reasoning profiles")
    agent = "summary"
    before = ai.llm_assignments()
    if agent not in before:
        pytest.skip(f"no {agent!r} agent")
    current = before[agent].get("llm_provider")
    target = next(p for p in profiles if current not in (p.uuid, p.name))

    snapshot = ai.assign_llm(target.name, agents=[agent])
    try:
        moved = ai.llm_assignments()[agent].get("llm_provider")
        assert moved in (target.uuid, target.name)
    finally:
        ai.restore_llm_assignments(snapshot)
    assert ai.llm_assignments()[agent].get("llm_provider") == current


def test_token_balance(ai):
    """FortiAI allowance, when the FortiAI proxy backs a profile (a balance query, not an LLM call)."""
    if not any(p.provider == "fortisoar" for p in ai.list_llm_configs()):
        pytest.skip("no connector-backed reasoning profile")
    try:
        balance = ai.token_balance()
    except (APIError, ValueError) as exc:
        pytest.skip(f"FortiAI balance unavailable: {exc}")
    assert balance.entitled_tokens >= 0
    assert 0 <= balance.remaining <= balance.entitled_tokens + balance.entitled_topup_tokens


# ------------------------------------------------------------------ MCP
def test_mcp_servers_and_status(ai):
    servers = ai.list_mcp_servers()
    assert servers, "no MCP servers registered"
    status = ai.mcp_status()
    assert status
    assert all(s.uuid or s.name for s in status)


def test_mcp_tool_catalog(ai):
    catalog = ai.mcp_tool_catalog()
    assert catalog, "no MCP tools discovered"
    for tool, meta in catalog.items():
        assert meta.get("server"), f"{tool}: no server"


def test_native_mcp_gateway_lists_tools(client, ai):
    """Built-in servers are reached through the appliance's /mcp/* gateway with this client's session."""
    tools = client.mcp.list_tools("soc")
    assert tools, "SOC Framework server lists no tools"


def test_registered_builtin_server_points_to_native_gateway(ai):
    """A built-in server stores no token (auth type FSR): the direct path must say to use client.mcp."""
    builtin = next((s for s in ai.mcp_status() if s.name == "SOC Framework"), None)
    if builtin is None:
        pytest.skip("no SOC Framework server registered")
    with pytest.raises(ValueError, match="client.mcp"):
        ai.list_registered_tools(builtin.uuid or builtin.name)


# ------------------------------------------------------ Organization Context
def test_organization_context_default_sop(client, ai):
    """The investigation planner falls back to INVESTIGATION_SOP / DEFAULT."""
    resp = client.get(
        "/api/3/organizational_contexts",
        params={"category": "INVESTIGATION_SOP", "subCategory": "DEFAULT", "$limit": 5},
    )
    rows = resp.get("hydra:member", []) if isinstance(resp, dict) else []
    if not rows:
        pytest.skip("no stock DEFAULT investigation SOP on this appliance")
    assert rows[0].get("content"), "DEFAULT SOP has no content"


# ------------------------------------------------------------------ traces
def test_trace_list_and_read(traces):
    listed = traces.list(limit=5)
    if not listed:
        pytest.skip("no traces on this appliance yet")
    first = listed[0]
    assert first.trace_id
    tree = traces.get(first.trace_id, depth=2)
    assert tree is not None
    spans = traces.spans(first.trace_id)
    assert spans, "a listed trace has no spans"
    assert all(s.trace_id in (None, first.trace_id) for s in spans)
    assert isinstance(traces.tokens(first.trace_id), dict)


def test_investigation_trace_token_attribution(traces):
    """Per-agent shares of an existing investigation add up to its root totals."""
    investigation = next((t for t in traces.list(limit=50) if t.name == "Alert Investigation"), None)
    if investigation is None:
        pytest.skip("no alert investigation among the latest traces")
    inv = traces.investigation(investigation.trace_id)
    assert inv.runs, "investigation has no agent runs"
    total = int(inv.tokens.get("total_tokens") or 0)
    by_agent = sum(row["total_tokens"] for row in inv.tokens_by_agent().values())
    assert by_agent == total
