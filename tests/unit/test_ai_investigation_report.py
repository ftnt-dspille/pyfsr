"""Unit tests for AIApi.investigation_report (verdict + tool calls + LLM-span health)."""

from types import SimpleNamespace

from pyfsr.api.ai import AIApi
from pyfsr.models import InvestigationReport, InvestigationResult, ToolCall, TraceSpan

TASK = "11111111-1111-1111-1111-111111111111"


def _api(spans, calls, *, status="completed", summary=None, llm_calls=()):
    client = SimpleNamespace(post=lambda *a, **k: {"hydra:member": spans})
    api = AIApi.__new__(AIApi)
    api.client = client
    api.get_status = lambda t: status
    summary = summary or {"classification": "Malicious", "confidence": 90, "highlighted_summary": "C2"}
    api.get_result = lambda t: InvestigationResult(task_id=t, status=status, summary=summary)
    api.investigation_tool_calls = lambda t: calls
    api.__dict__["_traces"] = SimpleNamespace(llm_calls=lambda t: list(llm_calls))
    return api


def _span(tokens, status="success", model="claude-haiku-5-5"):
    return {"totalTokens": tokens, "status": status, "provider": "anthropic", "modelName": model}


def test_report_collects_verdict_tools_and_span_health():
    calls = [ToolCall(tool_name="get_incident_by_id", server="FortiSIEM")]
    r = _api([_span(100), _span(50), _span(10, status="error")], calls).investigation_report(TASK)
    assert isinstance(r, InvestigationReport)
    assert (r.status, r.verdict, r.confidence, r.summary) == ("completed", "Malicious", 90, "C2")
    assert r.llm_spans == 3 and r.total_tokens == 160 and r.failed_spans == 1
    assert r.models == ["anthropic/claude-haiku-5-5"]
    assert r.tool_servers == ["FortiSIEM"] and r.conclusive


def test_completed_without_tool_calls_is_reported_not_hidden():
    r = _api([_span(5)], [], summary={"classification": "Inconclusive"}).investigation_report(TASK)
    assert r.tool_calls == [] and r.tool_servers == [] and not r.conclusive


def test_no_logs_and_no_llm_spans_still_reports_traced_tools():
    r = _api([], [ToolCall(tool_name="t", server="S", source="traces")]).investigation_report(TASK)
    assert r.llm_spans == 0 and r.models == [] and r.tool_servers == ["S"]


def test_801_reads_llm_calls_from_trace_spans_when_there_are_no_activity_logs():
    def span(i, o, status="OK"):
        attrs = {"model": "claude-haiku-5-5", "input_tokens": i, "output_tokens": o}
        return TraceSpan(
            span_id="s", span_type="LLM", status=status, attributes=attrs, output={"provider": "anthropic"}
        )

    r = _api([], [], llm_calls=[span(100, 10), span(50, 5), span(1, 1, status="ERROR")]).investigation_report(TASK)
    assert r.llm_spans == 3 and r.total_tokens == 167 and r.failed_spans == 1
    assert r.models == ["anthropic/claude-haiku-5-5"]


def test_running_investigation_is_not_conclusive():
    assert not _api([], [], status="running").investigation_report(TASK).conclusive
