"""Unit tests for the 8.0.1 AI surfaces: ``client.ai.traces`` and chat/orchestrator sessions."""

from datetime import datetime

import pytest

from pyfsr.api.ai import AgentSession, AIApi, AITracesAPI
from pyfsr.models import AgentTurn, ExecutionTree, TraceSpan


class FakeClient:
    """Records calls (with params/headers) and serves scripted responses.

    ``responses`` maps ``(METHOD, endpoint)`` to a value, or to a list consumed
    one item per call (for polling sequences).
    """

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def _serve(self, method, endpoint):
        value = self.responses.get((method, endpoint), {})
        if isinstance(value, list):
            return value.pop(0) if value else {}
        return value

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params, kw.get("headers")))
        return self._serve("GET", endpoint)

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data, kw.get("headers")))
        return self._serve("POST", endpoint)

    def delete(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("DELETE", endpoint, data, kw.get("headers")))
        return self._serve("DELETE", endpoint)


def _trace(tid, start):
    return {"trace_id": tid, "name": "Alert Investigation", "status": "OK", "start_time": start, "total_tokens": 10}


# ------------------------------------------------------------------ traces


def test_traces_is_a_cached_attribute_of_ai():
    ai = AIApi(FakeClient())
    assert isinstance(ai.traces, AITracesAPI)
    assert ai.traces is ai.traces


def test_list_sends_filters_and_types_rows():
    c = FakeClient({("GET", "/api/ai/traces/"): {"traces": [_trace("t1", "2026-09-24T17:40:14+00:00")]}})
    rows = AITracesAPI(c).list(status="OK", session_id="s1", limit=5)
    assert rows[0].trace_id == "t1" and rows[0].total_tokens == 10
    assert c.calls[0][2] == {"project_id": "default", "limit": 5, "status": "OK", "session_id": "s1"}


def test_list_rejects_out_of_range_limit():
    with pytest.raises(ValueError):
        AITracesAPI(FakeClient()).list(limit=501)


def test_iter_pages_with_epoch_cursor_from_iso_start_time():
    page1 = {"traces": [_trace("a", "2026-09-24T17:00:02+00:00"), _trace("b", "2026-09-24T17:00:01+00:00")]}
    page2 = {"traces": [_trace("c", "2026-09-24T17:00:00+00:00")]}
    c = FakeClient({("GET", "/api/ai/traces/"): [page1, page2]})
    ids = [t.trace_id for t in AITracesAPI(c).iter(page_size=2)]
    assert ids == ["a", "b", "c"]
    # second page's cursor is the last row's start_time as an epoch float string
    assert "cursor" not in c.calls[0][2]
    expected = datetime.fromisoformat("2026-09-24T17:00:01+00:00").timestamp()
    assert float(c.calls[1][2]["cursor"]) == pytest.approx(expected)


def test_iter_honours_max_items():
    page = {"traces": [_trace(str(i), f"2026-09-24T17:00:0{i}+00:00") for i in range(3)]}
    c = FakeClient({("GET", "/api/ai/traces/"): [page]})
    assert len(list(AITracesAPI(c).iter(page_size=3, max_items=2))) == 2


def test_execution_tree_walk_counts_and_find():
    tree = {
        "root_trace_id": "t1",
        "traces": ["t1", "t2"],
        "root": {
            "span_id": "r",
            "name": "Alert Investigation",
            "span_type": "AGENT",
            "children": [
                {"span_id": "w", "span_type": "LLM_WRAPPER", "children": [{"span_id": "l1", "span_type": "LLM"}]},
                {"span_id": "tool", "span_type": "TOOL"},
            ],
        },
    }
    c = FakeClient({("GET", "/api/ai/traces/t1/execution-tree"): tree})
    et = AITracesAPI(c).execution_tree("t1", max_traces=50)
    assert isinstance(et, ExecutionTree)
    assert et.total_steps == 4
    assert et.count_by_type() == {"AGENT": 1, "LLM_WRAPPER": 1, "LLM": 1, "TOOL": 1}
    assert [n.span_id for n in et.root.find(span_type="LLM")] == ["l1"]
    assert c.calls[0][2] == {"depth": 10, "io": "summary", "max_traces": 50}


def test_bad_io_mode_rejected_before_request():
    c = FakeClient()
    with pytest.raises(ValueError):
        AITracesAPI(c).execution_tree("t1", io="everything")
    assert c.calls == []


def test_span_properties_expose_llm_fields():
    span = TraceSpan.model_validate(
        {
            "span_id": "s",
            "span_type": "LLM",
            "output": {"provider": "FSRAI", "model": "", "usage": {"total_tokens": 42}},
        }
    )
    assert span.provider == "FSRAI"
    assert span.model is None  # empty string recorded on 8.0.1 -> None
    assert span.usage == {"total_tokens": 42}
    tool = TraceSpan.model_validate({"span_id": "t", "input": {"tool_call": {"name": "get_modules", "args": {}}}})
    assert tool.tool_call["name"] == "get_modules"


def test_llm_calls_fetches_each_llm_span():
    tree = {"root": {"span_id": "r", "span_type": "AGENT", "children": [{"span_id": "l1", "span_type": "LLM"}]}}
    c = FakeClient(
        {
            ("GET", "/api/ai/traces/t1/execution-tree"): tree,
            ("GET", "/api/ai/traces/spans/l1"): {"span_id": "l1", "span_type": "LLM"},
        }
    )
    calls = AITracesAPI(c).llm_calls("t1")
    assert [s.span_id for s in calls] == ["l1"]


@pytest.mark.parametrize("kwargs", [{}, {"days": 30, "before": "2026-01-01"}])
def test_purge_requires_exactly_one_cutoff(kwargs):
    with pytest.raises(ValueError):
        AITracesAPI(FakeClient()).purge_older_than(**kwargs)


def test_purge_sends_days_body():
    c = FakeClient({("DELETE", "/api/ai/traces/delete"): {"task_id": "x", "success": True}})
    assert AITracesAPI(c).purge_older_than(days=15)["success"] is True
    assert c.calls[0][:3] == ("DELETE", "/api/ai/traces/delete", {"days": 15})


def test_lineage_validates_direction():
    with pytest.raises(ValueError):
        AITracesAPI(FakeClient()).span_lineage("s", direction="sideways")


# ------------------------------------------------------------------ sessions


def _session_client(result, *, status_seq=("inprogress", "completed"), agent="orchestrator"):
    return FakeClient(
        {
            ("POST", f"/api/ai/agents/{agent}/trigger"): {"task_id": "task-1", "status": "pending"},
            ("GET", "/api/ai/agents/task-1/status"): [{"task_id": "task-1", "status": s} for s in status_seq],
            ("GET", "/api/ai/agents/task-1/result"): result,
        }
    )


def test_session_first_turn_payload_and_header():
    c = _session_client({"status": "success", "answer": "done", "request_id": "req-1"})
    s = AIApi(c).orchestrate(context={"pageName": "dashboard"}, user_id="csadmin", session_id="sess-1")
    turn = s.ask("list alerts", interval=0)
    method, endpoint, payload, headers = c.calls[0]
    assert (method, endpoint) == ("POST", "/api/ai/agents/orchestrator/trigger")
    assert payload == {
        "question": "list alerts",
        "previous_response_id": "",
        "request_id": "",
        "context": {"pageName": "dashboard"},
        "userId": "csadmin",
    }
    assert headers == {"X-CHAT-SESSION-ID": "sess-1"}
    assert turn.status == "success" and turn.answer == "done" and turn.task_id == "task-1"
    assert s.request_id == "req-1"


def test_clarification_reply_resumes_same_request():
    paused = {
        "status": "needs_clarification",
        "request_id": "req-9",
        "pending": {"type": "clarification", "question": "Which IP?", "expected_type": "entity", "round": 1},
    }
    c = _session_client(paused, status_seq=("completed",))
    s = AIApi(c).orchestrate()
    turn = s.ask("block the IP", interval=0)
    assert turn.needs_clarification and turn.is_paused and not turn.awaiting_approval
    assert turn.pending.question == "Which IP?"
    # second turn must carry the paused request_id so the plan resumes
    c.responses[("GET", "/api/ai/agents/task-1/status")] = [{"status": "completed"}]
    c.responses[("GET", "/api/ai/agents/task-1/result")] = {"status": "success", "request_id": "req-9"}
    s.reply("198.51.100.23", interval=0)
    second_payload = [call for call in c.calls if call[0] == "POST"][1][2]
    assert second_payload["question"] == "198.51.100.23"
    assert second_payload["request_id"] == "req-9"


@pytest.mark.parametrize(("method", "text"), [("approve", "approve"), ("deny", "deny")])
def test_approve_and_deny_send_text(method, text):
    c = _session_client({"status": "success"}, status_seq=("completed",))
    s = AIApi(c).orchestrate()
    getattr(s, method)(interval=0)
    assert c.calls[0][2]["question"] == text


def test_awaiting_approval_is_paused():
    turn = AgentTurn.model_validate({"status": "awaiting_approval", "pending": {"type": "approval"}})
    assert turn.awaiting_approval and turn.is_paused and not turn.needs_clarification


def test_timeout_returns_non_terminal_turn_without_result_fetch():
    c = _session_client({}, status_seq=("inprogress",) * 50, agent="conversation")
    s = AIApi(c).chat()
    turn = s.ask("hi", interval=0, timeout=0)
    assert turn.status in ("pending", "inprogress")
    assert not any(call[1].endswith("/result") for call in c.calls)
    assert c.calls[0][1] == "/api/ai/agents/conversation/trigger"


def test_chat_response_id_carried_to_next_turn():
    c = FakeClient(
        {
            ("POST", "/api/ai/agents/conversation/trigger"): {"task_id": "task-1", "status": "pending"},
            ("GET", "/api/ai/agents/task-1/status"): [{"status": "completed"}, {"status": "completed"}],
            ("GET", "/api/ai/agents/task-1/result"): [
                {"answer": "7 open alerts", "response_id": "resp-1"},
                {"answer": "ok", "response_id": "resp-2"},
            ],
        }
    )
    s = AIApi(c).chat()
    s.ask("how many open alerts?", interval=0)
    s.ask("and closed?", interval=0)
    posts = [call for call in c.calls if call[0] == "POST"]
    assert posts[1][2]["previous_response_id"] == "resp-1"
    assert s.previous_response_id == "resp-2"
    assert len(s.turns) == 2 and isinstance(s.last, AgentTurn)
    assert isinstance(s, AgentSession)


class _CursorBrokenClient(FakeClient):
    """First page OK; any request carrying a cursor fails like fsr-ai 8.0.1."""

    def __init__(self, first_page, message):
        super().__init__()
        self.first_page, self.message = first_page, message

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params, kw.get("headers")))
        if params and "cursor" in params:
            from types import SimpleNamespace

            from pyfsr.exceptions import APIError

            raise APIError(self.message, SimpleNamespace(status_code=500))
        return self.first_page


def test_iter_stops_with_warning_when_server_rejects_cursor():
    page = {"traces": [_trace("a", "2026-09-24T17:00:01+00:00"), _trace("b", "2026-09-24T17:00:00+00:00")]}
    c = _CursorBrokenClient(page, "operator does not exist: timestamp with time zone < numeric")
    with pytest.warns(UserWarning, match="cursor"):
        ids = [t.trace_id for t in AITracesAPI(c).iter(page_size=2)]
    assert ids == ["a", "b"]


def test_iter_reraises_other_cursor_errors():
    from pyfsr.exceptions import APIError

    page = {"traces": [_trace("a", "2026-09-24T17:00:01+00:00"), _trace("b", "2026-09-24T17:00:00+00:00")]}
    c = _CursorBrokenClient(page, "something else broke")
    with pytest.raises(APIError):
        list(AITracesAPI(c).iter(page_size=2))


def test_span_subtree_falls_back_to_trace_tree_on_404():
    from types import SimpleNamespace

    from pyfsr.exceptions import ResourceNotFoundError

    class C(FakeClient):
        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params, None))
            if endpoint.endswith("/tree"):
                raise ResourceNotFoundError("span not found", SimpleNamespace(status_code=404))
            if endpoint == "/api/ai/traces/spans/child":
                return {"span_id": "child", "trace_id": "t1"}
            if endpoint == "/api/ai/traces/t1":
                return {
                    "root": {
                        "span_id": "r",
                        "children": [{"span_id": "child", "name": "Org Context Search", "children": []}],
                    }
                }
            return {}

    c = C()
    node = AITracesAPI(c).span_subtree("child")
    assert node.span_id == "child" and node.name == "Org Context Search"
    assert ("GET", "/api/ai/traces/t1", {"depth": 50, "io": "summary"}, None) in c.calls


def test_trace_span_tool_result_reads_8_0_1_fields():
    from pyfsr.models import AgentToolResult, TraceSpan

    span = TraceSpan.model_validate(
        {
            "span_id": "s1",
            "span_type": "TOOL",
            "output": {
                "name": "get_alert",
                "status": "success",
                "result": "{}",
                "tool_call_id": None,
                "tool_results": [],
                "mcp_server_id": "bcd2462e-ad81-4307-b9b6-664e74219c10",
                "mcp_server_name": "SOC Framework",
            },
        }
    )
    tr = span.tool_result
    assert isinstance(tr, AgentToolResult)
    assert tr.mcp_server_name == "SOC Framework"
    assert tr.cached is False
    assert TraceSpan.model_validate({"span_id": "s2", "span_type": "LLM", "output": {}}).tool_result is None
