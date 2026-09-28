"""Unit tests for AIInsightsAPI (8.0.1 AI Insights)."""

from types import SimpleNamespace

import pytest

from pyfsr.api.ai import AIApi, AIInsightsAPI
from pyfsr.models import InsightPlan


class FakeClient:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def _serve(self, method, endpoint):
        value = self.responses.get((method, endpoint), {})
        if isinstance(value, list):
            return value.pop(0) if value else {}
        return value

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        return self._serve("GET", endpoint)

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return self._serve("POST", endpoint)

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return self._serve("PUT", endpoint)

    def delete(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("DELETE", endpoint, data))
        return self._serve("DELETE", endpoint)


_PLAN = {
    "objective": "o",
    "intent_interpretation": "i",
    "feasibility": {"status": "feasible", "reason": "ok"},
    "entities": [],
    "steps": [{"agent": "fortisoar-data-access"}],
}


def _flow_client(plan=_PLAN):
    return FakeClient(
        {
            ("POST", "/api/ai/insight/chain_of_thoughts"): {"chain_of_thought": ["a", "b"]},
            ("POST", "/api/ai/insight/plan"): plan,
            ("POST", "/api/ai/insight/plan/execute"): {"status": "accepted", "planid": "p-1", "task_id": "t-1"},
            ("GET", "/api/ai/insight/plan/t-1/status"): [{"status": "inprogress"}, {"status": "completed"}],
            ("GET", "/api/ai/insight/plan/t-1/result"): {
                "result": {"concise_summary": "none"},
                "execution_log": {},
                "last_executed": "2026-09-27T00:00:00Z",
            },
            ("POST", "/api/3/insights"): {"uuid": "ins-1"},
        }
    )


def test_insights_property_is_cached():
    ai = AIApi(FakeClient())
    assert isinstance(ai.insights, AIInsightsAPI) and ai.insights is ai.insights


def test_plan_generates_chain_of_thought_first():
    c = _flow_client()
    plan = AIInsightsAPI(c).plan("q", socrole="SOC Manager")
    assert c.calls[0] == ("POST", "/api/ai/insight/chain_of_thoughts", {"query": "q", "socrole": "SOC Manager"})
    assert c.calls[1][2] == {"query": "q", "chain_of_thought": ["a", "b"], "socrole": "SOC Manager"}
    assert plan.feasible and plan.chain_of_thought == ["a", "b"]


def test_create_runs_the_widget_flow_and_saves():
    c = _flow_client()
    run = AIInsightsAPI(c).create("t", query="q", interval=0)
    assert run.status == "completed" and run.result == {"concise_summary": "none"}
    assert run.planid == "p-1" and run.insight_id == "ins-1"
    execute = next(call for call in c.calls if call[1] == "/api/ai/insight/plan/execute")
    assert execute[2]["socrole"] == "SOC Analyst" and execute[2]["plan"]["objective"] == "o"
    assert (
        "POST",
        "/api/3/insights",
        {"title": "t", "query": "q", "socrole": "SOC Analyst", "planid": "p-1"},
    ) in c.calls


def test_create_refuses_infeasible_plan():
    c = _flow_client({**_PLAN, "feasibility": {"status": "infeasible", "reason": "no such data"}})
    with pytest.raises(ValueError, match="no such data"):
        AIInsightsAPI(c).create("t", query="q")
    assert not any(call[1].endswith("/execute") for call in c.calls)


def test_execute_without_wait_returns_handle():
    c = _flow_client()
    run = AIInsightsAPI(c).execute(InsightPlan.model_validate(_PLAN), wait=False)
    assert (run.task_id, run.planid, run.status) == ("t-1", "p-1", "accepted")
    assert not any("status" in call[1] for call in c.calls)


def test_save_update_uses_put():
    c = FakeClient({("PUT", "/api/3/insights/ins-1"): {"uuid": "ins-1"}})
    assert AIInsightsAPI(c).save("t", query="q", planid="p-2", insight_id="ins-1") == "ins-1"
    assert c.calls[0][0] == "PUT"


def test_trigger_resolves_owner_from_record():
    c = FakeClient(
        {
            ("GET", "/api/3/insights/ins-1"): {"createUser": {"@id": "/api/3/people/u-9"}},
            ("POST", "/api/ai/insight/trigger/schedule"): {"message": "Insight executed successfully."},
        }
    )
    assert AIInsightsAPI(c).trigger("ins-1") == "Insight executed successfully."
    assert c.calls[-1][2] == {"referenceid": "ins-1", "createUser": "u-9"}


def test_list_get_delete():
    c = FakeClient(
        {
            ("GET", "/api/ai/insight/"): [{"insight_id": "i1", "title": "A"}],
            ("GET", "/api/ai/insight/i1"): {"insight_id": "i1", "plan": {"steps": [1]}},
        }
    )
    api = AIInsightsAPI(c)
    assert [i.title for i in api.list()] == ["A"]
    assert api.get("i1").plan == {"steps": [1]}
    api.delete("i1")
    assert c.calls[-1][:2] == ("DELETE", "/api/ai/insight/i1")


def test_run_template_uses_stored_plan_and_role(monkeypatch):
    c = _flow_client()
    api = AIInsightsAPI(c)
    tpl = {"title": "High Risk Active Alerts", "plan": _PLAN, "socrole": "SOC Manager"}
    monkeypatch.setattr(api, "templates", lambda: [SimpleNamespace(get=tpl.get)])
    run = api.run_template("High Risk Active Alerts", interval=0)
    assert run.status == "completed"
    execute = next(call for call in c.calls if call[1] == "/api/ai/insight/plan/execute")
    assert execute[2]["socrole"] == "SOC Manager"
    assert not any(call[1].endswith("chain_of_thoughts") for call in c.calls)
    with pytest.raises(ValueError, match="no insight template"):
        api.run_template("nope")
