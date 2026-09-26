"""Unit tests for pyfsr.ai_eval: suite model, stub MCP listener, stub connector,
scoring and aggregation."""

import asyncio
import importlib
import importlib.util
import json
import socket
import sys
import threading
import time
import types

import pytest

from pyfsr.ai_eval import TokenPricing, aggregate, load_suite, score_run
from pyfsr.ai_eval.runner import STUB_DIR
from pyfsr.ai_eval.scenario import Suite
from pyfsr.models import AgentRun, InvestigationTrace, TracedToolCall


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


listener = _load("eval_stub_listener", STUB_DIR / "listener.py")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def suite():
    return load_suite()


# ------------------------------------------------------------------ suite
def test_default_suite_is_consistent(suite):
    assert {s.id for s in suite.scenarios} == {"fin-ws-lateral", "db-admin-change", "vpn-travel"}
    assert {k for k, s in suite.servers.items() if s.role == "distractor"} == {"billing", "crm"}
    fx = suite.fixtures()["servers"]
    assert fx["siem"]["name"] == "Eval SIEM" and fx["siem"]["tools"][0]["name"] == "siem_search_events"


def test_suite_rejects_a_fact_already_in_the_alert(suite):
    raw = suite.model_dump()
    raw["scenarios"][0]["facts"].append({"id": "leak", "match": "ws-fin-0417", "source": "edr"})
    with pytest.raises(ValueError, match="already appears in the alert"):
        Suite.model_validate(raw)


def test_suite_rejects_unknown_tool(suite):
    raw = suite.model_dump()
    raw["scenarios"][0]["expected_calls"].append({"server": "edr", "tool": "edr_nope"})
    with pytest.raises(ValueError, match="has no tool"):
        Suite.model_validate(raw)


@pytest.mark.parametrize(
    "tool,args,rule",
    [
        ("siem_search_events", {"query": "FS-FIN-02"}, 0),
        ("siem_search_events", {"query": "hostname=198.51.100.33", "hours": 48}, 0),
        ("siem_search_events", {"query": "WS-FIN-0417"}, 1),
        ("siem_search_events", {"query": "nobody"}, None),
        ("ti_lookup", {"indicator": "185.220.101.47"}, 0),
        ("idp_get_user", {"username": "SVC_DEPLOY"}, 1),
    ],
)
def test_scoring_matcher_agrees_with_listener(suite, tool, args, rule):
    key = suite.tool_server(tool)
    stub = next(t for t in suite.servers[key].tools if t.name == tool)
    result, index = stub.answer(args)
    served, served_index = listener.answer(stub.model_dump(), args)
    assert index == rule
    assert served == result and served_index == ("default" if rule is None else rule)


# ------------------------------------------------------------------ listener speaks MCP
def test_listener_over_the_real_mcp_client(suite, tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client import streamable_http

    connect = getattr(streamable_http, "streamable_http_client", None) or streamable_http.streamablehttp_client

    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text(json.dumps(suite.fixtures()))
    log = tmp_path / "calls.jsonl"
    port = _free_port()
    httpd = listener.serve(port, str(fixtures), str(log))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    async def session():
        async with connect(f"http://127.0.0.1:{port}/mcp/threat_intel") as (r, w, _):
            async with ClientSession(r, w) as s:
                init = await s.initialize()
                tools = await s.list_tools()
                hit = await s.call_tool("ti_lookup", {"indicator": "185.220.101.47"})
                miss = await s.call_tool("ti_lookup", {"indicator": "8.8.8.8"})
                return init, tools, hit, miss

    try:
        init, tools, hit, miss = asyncio.run(session())
    finally:
        httpd.shutdown()
    assert init.serverInfo.name == "Eval Threat Intel"
    assert [t.name for t in tools.tools] == ["ti_lookup"]
    assert tools.tools[0].inputSchema["required"] == ["indicator"]
    # fsr-ai's standard envelope, as the built-in servers answer
    assert json.loads(hit.content[0].text)["status"] == "success"
    assert json.loads(hit.content[0].text)["result"]["attribution"] == "QakBot C2 infrastructure"
    assert json.loads(miss.content[0].text)["result"] == {"verdict": "unknown", "score": None}
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [(r["server"], r["tool"], r["rule"]) for r in rows] == [
        ("threat_intel", "ti_lookup", 0),
        ("threat_intel", "ti_lookup", "default"),
    ]


def test_listener_envelope_modes(suite):
    ti = suite.fixtures()["servers"]["threat_intel"]
    call = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "ti_lookup", "arguments": {"indicator": "185.220.101.47"}},
    }
    wrapped = json.loads(listener.handle_rpc(ti, "threat_intel", call, None)["result"]["content"][0]["text"])
    assert wrapped["status"] == "success" and wrapped["result"]["score"] == 92
    raw = json.loads(
        listener.handle_rpc({**ti, "envelope": "raw"}, "threat_intel", call, None)["result"]["content"][0]["text"]
    )
    assert raw["score"] == 92 and "status" not in raw
    # fsr-ai hands a non-dict envelope result over as-is, so it is sent pre-serialised
    assert listener.envelope([1, 2]) == {"status": "success", "result": "[1, 2]", "error": None}


def test_listener_unknown_server_is_404(suite, tmp_path):
    import urllib.error
    import urllib.request

    fixtures = tmp_path / "f.json"
    fixtures.write_text(json.dumps(suite.fixtures()))
    port = _free_port()
    httpd = listener.serve(port, str(fixtures), None)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp/nope", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 404
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            assert "siem" in json.loads(r.read())["servers"]
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ the connector
@pytest.fixture
def stub_connector(monkeypatch, tmp_path):
    """Import the connector package with a fake ``connectors.core.connector``."""
    core = types.ModuleType("connectors.core.connector")

    class ConnectorError(Exception):
        pass

    core.Connector = object
    core.ConnectorError = ConnectorError
    core.get_logger = lambda name: None
    for name, mod in {
        "connectors": types.ModuleType("connectors"),
        "connectors.core": types.ModuleType("connectors.core"),
        "connectors.core.connector": core,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    pkg = "fsr_ai_eval_stub_under_test"
    spec = importlib.util.spec_from_file_location(
        pkg, STUB_DIR / "__init__.py", submodule_search_locations=[str(STUB_DIR)]
    )
    monkeypatch.setitem(sys.modules, pkg, importlib.util.module_from_spec(spec))
    ops = importlib.import_module(f"{pkg}.operations")
    conn = importlib.import_module(f"{pkg}.connector")
    state = tmp_path / "state"
    monkeypatch.setattr(ops, "_state_dir", lambda: (state.mkdir(exist_ok=True), str(state))[1])
    return ops, conn


def test_connector_starts_listener_loads_fixtures_and_reads_log(stub_connector, suite):
    ops, conn = stub_connector
    config = {"port": _free_port()}
    stub = conn.EvalStub()
    try:
        loaded = stub.execute(config, "load_fixtures", {"fixtures": json.dumps(suite.fixtures())})
        assert loaded["running"] and "siem" in loaded["servers"] and loaded["tools"] == 15
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "cmdb_change_requests", "arguments": {"asset": "SRV-DB-11"}},
        }
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{config['port']}/mcp/cmdb",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            text = json.loads(r.read())["result"]["content"][0]["text"]
        assert "CHG0048812" in text
        log = stub.execute(config, "get_call_log", {"clear": True})
        assert log["count"] == 1 and log["calls"][0]["tool"] == "cmdb_change_requests"
        assert stub.execute(config, "get_call_log", {})["count"] == 0
        assert stub.execute(config, "start_server", {})["started"] is False  # idempotent
    finally:
        stub.on_deactivate(config)
    for _ in range(20):
        if not ops._listening(config["port"]):
            break
        time.sleep(0.1)
    assert not ops._listening(config["port"])


def test_connector_rejects_bad_fixtures(stub_connector):
    _, conn = stub_connector
    with pytest.raises(Exception, match="fixtures must be"):
        conn.EvalStub().execute({"port": _free_port()}, "load_fixtures", {"fixtures": {"nope": 1}})


# ------------------------------------------------------------------ scoring
def _call(tool, args, *, server, output=None, by="llm", agent="Query SIEM", error=None):
    return TracedToolCall(
        tool_name=tool, args=args, server=server, output=output, selected_by=by, agent=agent, error=error
    )


def _investigation(suite, *, chained=True):
    calls_ti = [
        _call(
            "ti_lookup",
            {"indicator": "185.220.101.47"},
            server="Eval Threat Intel",
            output={"attribution": "QakBot C2 infrastructure"},
            by="code",
        )
    ]
    calls_edr = [
        _call(
            "edr_process_tree",
            {"hostname": "WS-FIN-0417"},
            server="Eval EDR",
            output={"file_opened": "Invoice_Q3_8841.docm"},
        ),
        _call(
            "edr_network_connections",
            {"hostname": "WS-FIN-0417"},
            server="Eval EDR",
            output={"dst_ip": "198.51.100.33"},
        ),
    ]
    calls_siem = [
        _call(
            "siem_search_events",
            {"query": "198.51.100.33" if chained else "WS-FIN-0417"},
            server="Eval SIEM",
            output={"share": "payroll"} if chained else {"events": []},
            by="llm-chained",
        ),
        _call("billing_cost_by_service", {"account": "x"}, server="Eval Cloud Billing", output={}),
        _call(
            "siem_search_events", {"query": "hunt"}, server="Eval SIEM", error="Tool not allowed : siem_search_events"
        ),
        _call("query_records", {"module": "alerts"}, server="FortiSOAR Module Management", output={}),
    ]
    runs = [
        AgentRun(trace_id="t0", agent="Alert Investigation"),
        AgentRun(
            trace_id="t1",
            agent="Threat Intelligence Provider",
            question="Is the IP malicious?",
            answer="Yes, QakBot C2",
            tool_calls=calls_ti,
        ),
        AgentRun(
            trace_id="t2",
            agent="Query Endpoint",
            question="What ran?",
            answer="winword opened Invoice_Q3_8841.docm",
            tool_calls=calls_edr,
        ),
        AgentRun(
            trace_id="t3",
            agent="Query SIEM",
            question="Lateral movement?",
            answer="Payroll share read" if chained else "No information available",
            tool_calls=calls_siem,
        ),
    ]
    return InvestigationTrace(task_id="task-1", status="OK", runs=runs)


def test_score_run_measures_tool_choice_pivot_and_fact_use(suite):
    sc = suite.scenario("fin-ws-lateral")
    s = score_run(
        suite,
        sc,
        _investigation(suite),
        summary={"text": "QakBot dropper exfiltrated payroll data"},
        classification="Malicious",
    )
    assert s.verdict == 1.0
    assert s.eval_calls == 6  # the FortiSOAR Module Management call is not ours
    assert (s.distractor_calls, s.failed_calls, s.wasted_calls) == (1, 1, 2)
    assert s.precision == round(4 / 6, 3)
    exp = {e.tool: e for e in s.expected}
    assert exp["siem_search_events"].right_query and exp["siem_search_events"].selected_by == "llm-chained"
    assert not exp["cmdb_lookup_asset"].called
    assert s.tool_recall == 0.8 and s.query_accuracy == 0.8 and s.chain_recall == 0.5
    facts = {f.id: f for f in s.facts}
    assert facts["payroll"].retrieved and facts["payroll"].in_answers and facts["payroll"].in_summary
    assert facts["pivot"].retrieved and not facts["pivot"].in_answers  # retrieved, never mentioned
    assert s.lost_facts == ["pivot"]
    assert s.fact_retrieval == 0.8 and s.fact_use == 0.6
    assert 0 < s.composite < 1


def test_score_run_without_the_pivot(suite):
    sc = suite.scenario("fin-ws-lateral")
    s = score_run(suite, sc, _investigation(suite, chained=False), summary={}, classification="Suspicious")
    assert s.verdict == 0.5 and s.chain_recall == 0.0
    assert not {f.id: f for f in s.facts}["payroll"].retrieved


def test_score_run_with_no_trace(suite):
    s = score_run(suite, suite.scenario("vpn-travel"), None, classification="Benign")
    assert s.error == "no trace" and s.composite == 0.3


def test_aggregate_reports_spread_and_hit_rates(suite):
    sc = suite.scenario("fin-ws-lateral")
    runs = [
        score_run(suite, sc, _investigation(suite), summary={}, classification="Malicious"),
        score_run(suite, sc, _investigation(suite, chained=False), summary={}, classification="Suspicious"),
    ]
    agg = aggregate(runs)
    row = agg["scenarios"]["fin-ws-lateral"]
    assert row["runs"] == 2 and row["verdicts"] == {"Malicious": 1, "Suspicious": 1}
    siem = next(e for e in row["expected_calls"] if e["tool"] == "siem_search_events")
    assert siem["called"] == 1.0 and siem["right_query"] == 0.5
    assert next(f for f in row["facts"] if f["id"] == "payroll")["retrieved"] == 0.5
    assert row["tools_used"]["billing.billing_cost_by_service"] == 2
    assert agg["overall"]["max"] >= agg["overall"]["min"]


# ------------------------------------------------------------------ cost


def test_pricing_defaults_to_fortiai_rates():
    p = TokenPricing()
    assert p.cost(40_000, 10_000) == 10.0  # 50k tokens at $200/M
    assert p.cost(40_000, 10_000, billed_tokens=100_000) == 20.0  # the metered count wins
    split = TokenPricing(input_usd_per_million=2.0, output_usd_per_million=8.0)
    assert split.cost(1_000_000, 100_000) == 2.8


def test_score_and_aggregate_carry_tokens_and_budget(suite):
    sc = suite.scenario("fin-ws-lateral")
    inv = _investigation(suite)
    inv.tokens = {"input_tokens": 40_000, "output_tokens": 10_000, "total_tokens": 50_000, "llm_calls": 40}
    traced = score_run(suite, sc, inv, summary={}, classification="Malicious")
    assert traced.total_tokens == 50_000 and traced.cost_usd == 10.0 and traced.metered_tokens is None
    metered = score_run(suite, sc, inv, summary={}, classification="Malicious", metered_tokens=100_000)
    assert metered.cost_usd == 20.0

    assert aggregate([traced])["budget"] == {
        "tokens_per_investigation": 50_000,
        "usd_per_investigation": 10.0,
        "investigations_per_free_month": 100,
        "source": "traces",
    }
    both = aggregate([traced, metered])
    assert both["budget"]["tokens_per_investigation"] == 75_000 and both["budget"]["source"] == "traces"
    assert aggregate([metered])["budget"]["source"] == "metered"
    assert both["scenarios"]["fin-ws-lateral"]["total_tokens"]["mean"] == 50_000


def _llm_profiles(provider="fortisoar", connector="fortinet-fortiai-proxy"):
    return [
        {
            "uuid": "p1",
            "name": "Low Reasoning",
            "isdefault": True,
            "provider": provider,
            "config": {"connector_name": connector, "connector_config_id": "cfg-1"},
        },
    ]


def test_token_balance_reads_the_fortiai_meter():
    from types import SimpleNamespace

    from pyfsr.api.ai import AIApi

    executed = []

    class Client:
        def get(self, endpoint, params=None, **kw):
            assert endpoint == "/api/ai/llm/config"
            return _llm_profiles()

        class connectors:  # noqa: N801
            @staticmethod
            def execute(connector, operation, *, config=None, params=None):
                executed.append((connector, operation, config))
                return SimpleNamespace(
                    data={"entitled_tokens": 5_000_000, "remain_tokens": 4_400_000, "type": "DEVICE_LEVEL"}
                )

    bal = AIApi(Client()).token_balance()
    assert executed == [("fortinet-fortiai-proxy", "get_token_balance_info", "cfg-1")]
    assert bal.used_tokens == 600_000 and bal.remaining == 4_400_000


def test_token_balance_refuses_a_native_provider_profile():
    from pyfsr.api.ai import AIApi

    class Client:
        def get(self, endpoint, params=None, **kw):
            return _llm_profiles(provider="openai", connector=None)

    with pytest.raises(ValueError, match="not the FortiAI proxy"):
        AIApi(Client()).token_balance()
