"""Unit tests for rebuilding an investigation from 8.0.1 traces.

Span shapes mirror what fsr-ai 8.0.1 records (live-verified on 8.0.1): the agent
input as ``input_data.preview`` JSON text, the LLM's tool pick in
``output.tools``/``output.tool_name``, masked ``SM_..._EM`` arguments on the LLM
side, and ``mcp_server_name`` on the TOOL span output.
"""

import json
from types import SimpleNamespace

from pyfsr.api.ai import AIApi, AITracesAPI
from pyfsr.exceptions import APIError
from pyfsr.models import AgentRun, InvestigationTrace, ToolCall

TASK = "11111111-1111-1111-1111-111111111111"
SUB = "22222222-2222-2222-2222-222222222222"
ALERT = "33333333-3333-3333-3333-333333333333"


class FakeClient:
    """Serves ``(METHOD, endpoint)`` responses; a value that is an exception is raised."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        value = self.responses.get(("GET", endpoint), {})
        if isinstance(value, Exception):
            raise value
        return value


def _not_found():
    return APIError("Not Found", SimpleNamespace(status_code=404))


def _span(span_id, span_type, start, *, parent="root", name=None, status="OK", input=None, output=None, error=None):
    return {
        "span_id": span_id,
        "parent_span_id": parent,
        "span_type": span_type,
        "name": name or {"TOOL": "Tool Execution", "LLM": "LLM Call"}.get(span_type, span_type),
        "status": status,
        "start_time": f"2026-09-25T17:36:{start:02d}+00:00",
        "input": input,
        "output": output,
        "error": error,
    }


def _tool(span_id, start, name, args, *, server="SOC Framework", result="[]", error=None):
    return _span(
        span_id,
        "TOOL",
        start,
        input={"tool_call": {"name": name, "args": args}},
        output={
            "name": name,
            "args": args,
            "result": result,
            "error": error,
            "cached": False,
            "mcp_server_name": server,
            "mcp_server_id": "srv-1" if server else None,
        },
        status="ERROR" if error else "OK",
    )


def _llm(span_id, start, *, picks=(), saw_tool_result=False):
    messages = [{"role": "user", "content": "Find out reputation of provided indicator : SM_FILENAME_ab12_EM"}]
    if saw_tool_result:
        messages.append({"role": "tool", "content": "[...]", "tool_call_id": "c1"})
    payload = {"params": {"messages": messages, "tool_choice": {"choice": "auto"}}}
    tools = [{"name": p, "args": {"indicators": ["SM_FILENAME_ab12_EM"]}} for p in picks]
    output = {"tools": tools or None, "tool_name": picks[0] if picks else None, "content": None if picks else "{}"}
    return _span(span_id, "LLM", start, parent="wrap", input={"payload": json.dumps(payload)}, output=output)


QUESTION = "Do threat intelligence feeds associate 'powershell.exe' or 'winword.exe' with malicious activity?"


def _provider_spans(*, preview=None):
    preview = preview or json.dumps({"ioc": [{"type": "File", "value": "powershell.exe"}], "question": QUESTION})
    return [
        _span(
            "root",
            "AGENT",
            0,
            parent=None,
            name="Threat Intelligence Provider",
            input={"kwargs": {"trace_id": SUB}, "input_data": {"preview": preview}},
            output={"answer": "No", "evidence": "No reputation available.", "confidence": "100%"},
        ),
        # fixed lookups, run by agent code before any LLM step
        _tool("t1", 1, "get_indicators", {"values": ["powershell.exe", "winword.exe"]}, result='[{"id": 3}]'),
        _tool("t2", 2, "enrich_indicator", {"params": {"indicator_value": "powershell.exe"}}),
        # blind one-shot pick, then the call it asked for
        _llm("l1", 3, picks=("get_alerts_linked_to_indicators",)),
        _tool("t3", 4, "get_alerts_linked_to_indicators", {"indicators": ["powershell.exe"]}),
        # a pick made after reading a tool result
        _llm("l2", 5, picks=("get_alerts_linked_to_indicators",), saw_tool_result=True),
        _tool("t4", 6, "get_alerts_linked_to_indicators", {"indicators": ["winword.exe"]}),
        # refused before dispatch: no server on the result
        _tool("t5", 7, "hunt_ioc_siem", {"params": {}}, server=None, error="Tool not allowed : hunt_ioc_siem"),
        _span("m1", "AGENT", 8, name="Data Masking", status="ERROR", error="noise"),
        _llm("l3", 9),
    ]


def _investigation_client(extra=None):
    responses = {
        ("GET", f"/api/ai/traces/{TASK}/execution-tree"): {"root_trace_id": TASK, "traces": [SUB]},
        ("GET", f"/api/ai/traces/{TASK}/spans"): {
            "spans": [_span("r0", "AGENT", 0, parent=None, name="Alert Investigation", output={})]
        },
        ("GET", f"/api/ai/traces/{SUB}/spans"): {"spans": _provider_spans()},
        # the root trace's totals include the sub-run's
        ("GET", f"/api/ai/traces/tokens/{TASK}"): {
            "input_tokens": 500,
            "output_tokens": 50,
            "total_tokens": 550,
            "llm_calls": 7,
        },
        ("GET", f"/api/ai/traces/tokens/{SUB}"): {
            "input_tokens": 400,
            "output_tokens": 40,
            "total_tokens": 440,
            "llm_calls": 4,
        },
    }
    responses.update(extra or {})
    return FakeClient(responses)


# ------------------------------------------------------------- agent_run


def test_agent_run_labels_who_chose_each_call():
    run = AITracesAPI(FakeClient({("GET", f"/api/ai/traces/{SUB}/spans"): {"spans": _provider_spans()}})).agent_run(SUB)
    assert isinstance(run, AgentRun)
    assert [(c.tool_name, c.selected_by) for c in run.tool_calls] == [
        ("get_indicators", "code"),
        ("enrich_indicator", "code"),
        ("get_alerts_linked_to_indicators", "llm"),
        ("get_alerts_linked_to_indicators", "llm-chained"),
        ("hunt_ioc_siem", "code"),
    ]
    assert run.llm_picks == 2 and run.chained is True
    assert run.llm_calls == 3
    assert run.steps[:3] == ["T:get_indicators", "T:enrich_indicator", "LLM->get_alerts_linked_to_indicators"]


def test_agent_run_reads_question_answer_and_tool_details():
    run = AITracesAPI(FakeClient({("GET", f"/api/ai/traces/{SUB}/spans"): {"spans": _provider_spans()}})).agent_run(SUB)
    assert run.agent == "Threat Intelligence Provider"
    assert run.question == QUESTION
    assert (run.answer, run.confidence) == ("No", "100%")
    first = run.tool_calls[0]
    assert first.server == "SOC Framework" and first.output == [{"id": 3}] and first.result_chars == 11
    assert first.agent == run.agent and first.question == QUESTION
    refused = run.tool_calls[-1]
    assert refused.failed and refused.server is None


def test_agent_run_errors_skip_masking_plumbing():
    run = AITracesAPI(FakeClient({("GET", f"/api/ai/traces/{SUB}/spans"): {"spans": _provider_spans()}})).agent_run(SUB)
    assert [e["span"] for e in run.errors] == ["Tool Execution"]


def test_question_recovered_from_truncated_preview():
    cut = '{"ioc": [], "question": "Is host \\"WS-042\\" critical?", "context": {"data": {"event_co'
    spans = _provider_spans(preview=cut)
    run = AITracesAPI(FakeClient({("GET", f"/api/ai/traces/{SUB}/spans"): {"spans": spans}})).agent_run(SUB)
    assert run.question == 'Is host "WS-042" critical?'


def test_no_information_answer_flag():
    spans = _provider_spans()
    spans[0]["output"] = {"answer": "No information available", "evidence": "", "confidence": "0%"}
    run = AITracesAPI(FakeClient({("GET", f"/api/ai/traces/{SUB}/spans"): {"spans": spans}})).agent_run(SUB)
    assert run.no_information


# --------------------------------------------------------- investigation


def test_investigation_walks_every_sub_trace_and_totals_metrics():
    inv = AITracesAPI(_investigation_client()).investigation(TASK)
    assert isinstance(inv, InvestigationTrace)
    assert [r.agent for r in inv.runs] == ["Alert Investigation", "Threat Intelligence Provider"]
    assert len(inv.questions) == 1 and len(inv.tool_calls) == 5
    m = inv.metrics()
    assert m["selected_by"] == {"code": 3, "llm": 1, "llm-chained": 1}
    assert m["questions_multi_tool"] == 1 and m["questions_multi_llm_pick"] == 1 and m["questions_chained"] == 1
    assert m["max_tools_per_question"] == 5 and m["failed_tool_calls"] == 1
    assert m["servers"] == {"SOC Framework": 4, "(unattributed)": 1}
    assert m["tokens"]["total_tokens"] == 550 and m["tokens"]["llm_calls"] == 7
    # per agent: the root keeps only its own share, so the parts add up to the total
    assert m["tokens_by_agent"] == {
        "Threat Intelligence Provider": {"input_tokens": 400, "output_tokens": 40, "total_tokens": 440, "llm_calls": 4},
        "Alert Investigation": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110, "llm_calls": 3},
    }


def test_investigation_tolerates_missing_token_totals():
    c = _investigation_client({("GET", f"/api/ai/traces/tokens/{TASK}"): _not_found()})
    inv = AITracesAPI(c).investigation(TASK)
    assert inv.tokens == {} and inv.runs[1].tokens["llm_calls"] == 4


# ------------------------------------------- AIApi helpers: traces first


def test_investigation_tool_calls_prefers_traces():
    calls = AIApi(_investigation_client()).investigation_tool_calls(TASK)
    assert all(isinstance(c, ToolCall) and c.source == "traces" for c in calls)
    assert calls[3].selected_by == "llm-chained" and calls[3].agent == "Threat Intelligence Provider"
    assert calls[0].server == "SOC Framework" and calls[0].correlation_id == TASK


def test_investigation_tool_calls_falls_back_to_activity_logs_without_traces():
    log = {"response": json.dumps({"tool_name": "hunt_ioc_siem", "tool_args": {}}), "correlationID": TASK}
    c = FakeClient(
        {
            ("GET", f"/api/ai/traces/{TASK}/execution-tree"): _not_found(),
            ("GET", "/api/3/llm_activity_logs"): {"hydra:member": [log]},
        }
    )
    calls = AIApi(c).investigation_tool_calls(TASK)
    assert [(x.tool_name, x.source) for x in calls] == [("hunt_ioc_siem", "llm_activity_logs")]


def test_attribute_tool_calls_skips_catalog_for_traced_calls():
    c = _investigation_client()
    calls = AIApi(c).attribute_tool_calls(TASK)
    assert len(calls) == 5
    assert not any("/api/ai/mcp" in call[1] for call in c.calls)  # no server probing


def test_find_investigations_falls_back_to_traces():
    rows = [
        {
            "trace_id": TASK,
            "name": "Alert Investigation",
            "status": "OK",
            "root_span_id": "r0",
            "start_time": "2026-09-25T17:33:52+00:00",
        },
        {"trace_id": "other", "name": "Alert Investigation", "status": "OK", "root_span_id": "r9"},
        {"trace_id": "chat", "name": "Chat Assistant", "status": "OK", "root_span_id": "r8"},
    ]
    c = FakeClient(
        {
            ("GET", "/api/3/llm_activity_logs"): {"hydra:member": []},
            ("GET", "/api/ai/traces/"): {"traces": rows},
            ("GET", "/api/ai/traces/spans/r0"): {"span_id": "r0", "input": {"alert": {"uuid": ALERT}}},
            ("GET", "/api/ai/traces/spans/r9"): {"span_id": "r9", "input": {"alert": {"uuid": "someone-else"}}},
        }
    )
    found = AIApi(c).find_investigations(ALERT)
    assert [(f["task_id"], f["source"]) for f in found] == [(TASK, "traces")]
    assert not any(call[1] == "/api/ai/traces/spans/r8" for call in c.calls)  # chats not opened


def test_find_investigations_keeps_activity_log_rows_when_present():
    c = FakeClient({("GET", "/api/3/llm_activity_logs"): {"hydra:member": [{"correlationID": TASK}]}})
    assert AIApi(c).find_investigations(ALERT) == [{"task_id": TASK, "log_count": 1}]
    assert not any(call[1].startswith("/api/ai/traces") for call in c.calls)
