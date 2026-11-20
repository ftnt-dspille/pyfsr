"""Unit tests for AIApi.investigation_report (verdict + tool calls + LLM-span health)."""

from types import SimpleNamespace

from pyfsr.api.ai import AIApi
from pyfsr.models import InvestigationReport, InvestigationResult, ToolCall

TASK = "11111111-1111-1111-1111-111111111111"


def _api(spans, calls, *, status="completed", summary=None):
    client = SimpleNamespace(post=lambda *a, **k: {"hydra:member": spans})
    api = AIApi.__new__(AIApi)
    api.client = client
    api.get_status = lambda t: status
    summary = summary or {"classification": "Malicious", "confidence": 90, "highlighted_summary": "C2"}
    api.get_result = lambda t: InvestigationResult(task_id=t, status=status, summary=summary)
    api.investigation_tool_calls = lambda t: calls
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


def test_no_logs_on_801_still_reports_traced_tools():
    r = _api([], [ToolCall(tool_name="t", server="S", source="traces")]).investigation_report(TASK)
    assert r.llm_spans == 0 and r.models == [] and r.tool_servers == ["S"]


def test_running_investigation_is_not_conclusive():
    assert not _api([], [], status="running").investigation_report(TASK).conclusive
