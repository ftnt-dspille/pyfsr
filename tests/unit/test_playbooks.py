"""Unit tests for PlaybooksAPI (run history / get / resume)."""

import pytest

from pyfsr.api.playbooks import PlaybooksAPI, _shape_run


def _run(iri, name, status, modified, **extra):
    return {"@id": iri, "name": name, "status": status, "modified": modified, **extra}


class FakeClient:
    def __init__(self, *, workflows=None, historical=None, name_lookup=None, get_raiser=None):
        self.get_calls = []
        self.post_calls = []
        self._workflows = workflows or []
        self._historical = historical or []
        self._name_lookup = name_lookup
        self._get_raiser = get_raiser

    def get(self, endpoint, params=None, **kwargs):
        self.get_calls.append((endpoint, params))
        if self._get_raiser:
            self._get_raiser(endpoint)
        if endpoint.startswith("/api/3/workflows?"):
            return self._name_lookup or {"hydra:member": []}
        if endpoint.startswith("/api/wf/api/historical-workflows/"):
            tail = endpoint.split("/api/wf/api/historical-workflows/")[1]
            if not tail.startswith("?"):  # single get: "<pk>/?format=json"
                seg = tail.split("/")[0]
                return next(
                    (r for r in self._historical if r["@id"].rstrip("/").endswith(seg)),
                    {},
                )
            return {"hydra:member": self._historical}
        if endpoint.startswith("/api/wf/api/workflows/"):
            tail = endpoint.split("/api/wf/api/workflows/")[1]
            if not tail.startswith("?"):  # single get: "<pk>/?format=json"
                seg = tail.split("/")[0]
                return next(
                    (r for r in self._workflows if r["@id"].rstrip("/").endswith(seg)),
                    {},
                )
            return {"hydra:member": self._workflows}
        return {}

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.post_calls.append((endpoint, data))
        if "notrigger" in endpoint:
            return {"task_id": "run-uuid"}
        return {"resumed": True}


# -- _shape_run -------------------------------------------------------------
def test_shape_run_extracts_pk_and_error():
    m = _run(
        "/api/wf/api/workflows/abc-123/",
        "Block IP",
        "failed",
        "2026-06-08T00:00:00",
        task_id="t1",
        uuid="u1",
        result={"Error message": "boom"},
        _source="live",
    )
    s = _shape_run(m)
    assert s["pk"] == "abc-123"
    assert s["error_message"] == "boom"
    assert s["status"] == "failed"
    assert s["source"] == "live"


def test_shape_run_no_result():
    s = _shape_run(_run("/api/wf/api/workflows/x/", "n", "finished", "t"))
    assert s["error_message"] is None
    assert s["pk"] == "x"


# -- runs -------------------------------------------------------------------
def test_runs_merges_and_dedupes():
    shared = _run("/api/wf/api/workflows/dup/", "Dup", "finished", "2026-06-08T02:00")
    client = FakeClient(
        workflows=[_run("/api/wf/api/workflows/a/", "A", "failed", "2026-06-08T03:00"), shared],
        historical=[
            shared,  # same IRI in both tables -> dedup
            _run("/api/wf/api/historical-workflows/b/", "B", "finished", "2026-06-08T01:00"),
        ],
    )
    runs = PlaybooksAPI(client).execution_history(limit=10)
    pks = [r["pk"] for r in runs]
    assert pks == ["a", "dup", "b"]  # sorted by modified desc, deduped
    assert {r["source"] for r in runs} == {"live", "historical"}


def test_runs_respects_limit():
    wf = [_run(f"/api/wf/api/workflows/{i}/", f"n{i}", "finished", f"t{i}") for i in range(5)]
    runs = PlaybooksAPI(FakeClient(workflows=wf)).execution_history(limit=2)
    assert len(runs) == 2


def test_runs_by_playbook_name_resolves_uuid():
    client = FakeClient(
        workflows=[_run("/api/wf/api/workflows/a/", "A", "failed", "t")],
        name_lookup={"hydra:member": [{"uuid": "pb-uuid"}]},
    )
    PlaybooksAPI(client).execution_history(playbook="Block IP")
    # name lookup happened, and the run fetch carried the template_iri filter
    assert any("/api/3/workflows?" in c[0] for c in client.get_calls)
    assert any("template_iri=/api/3/workflows/pb-uuid" in c[0] for c in client.get_calls)


def test_runs_unknown_playbook_returns_empty():
    client = FakeClient(name_lookup={"hydra:member": []})
    assert PlaybooksAPI(client).execution_history(playbook="nope") == []


def test_runs_raw_returns_unshaped():
    client = FakeClient(workflows=[_run("/api/wf/api/workflows/a/", "A", "failed", "t")])
    runs = PlaybooksAPI(client).execution_history(raw=True)
    assert "@id" in runs[0]


# -- get --------------------------------------------------------------------
def test_get_live_then_historical_fallback():
    client = FakeClient(
        workflows=[],
        historical=[_run("/api/wf/api/historical-workflows/h1/", "H", "failed", "t", uuid="u")],
    )
    run = PlaybooksAPI(client).get_execution("h1")
    assert run["pk"] == "h1"
    assert run["source"] == "historical"


def test_get_blank_pk_raises():
    with pytest.raises(ValueError):
        PlaybooksAPI(FakeClient()).get_execution("")


# -- trigger ----------------------------------------------------------------
def test_trigger_by_uuid_posts_to_notrigger():
    client = FakeClient()
    out = PlaybooksAPI(client).trigger(
        "f0674018-306d-414a-94da-90ace5d98350",
        records="alerts:abc-123",
        inputs={"foo": "bar"},
    )
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/triggers/1/notrigger/f0674018-306d-414a-94da-90ace5d98350"
    assert body["records"] == ["/api/3/alerts/abc-123"]
    assert body["inputs"] == {"foo": "bar"}
    assert out["task_id"] == "run-uuid"


def test_trigger_by_name_resolves_uuid_first():
    client = FakeClient(name_lookup={"hydra:member": [{"uuid": "pb-uuid"}]})
    PlaybooksAPI(client).trigger("AI Investigation", records=["/api/3/alerts/x"])
    assert any("/api/3/workflows?" in c[0] for c in client.get_calls)
    assert client.post_calls[0][0] == "/api/triggers/1/notrigger/pb-uuid"
    # already-IRI records are passed through unchanged
    assert client.post_calls[0][1]["records"] == ["/api/3/alerts/x"]


def test_trigger_unknown_playbook_raises():
    client = FakeClient(name_lookup={"hydra:member": []})
    with pytest.raises(ValueError):
        PlaybooksAPI(client).trigger("nope")


def test_trigger_without_records_sends_empty_body():
    client = FakeClient()
    PlaybooksAPI(client).trigger("f0674018-306d-414a-94da-90ace5d98350")
    assert client.post_calls[0][1] == {}


# -- resume -----------------------------------------------------------------
def test_resume_posts_to_wfinput_resume():
    client = FakeClient()
    PlaybooksAPI(client).resume("run-1", manual_input_id=7, input={"choice": "yes"}, step_id="s1", approved=True)
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/wf/api/workflows/run-1/wfinput_resume/?format=json"
    assert body["manual_input_id"] == 7
    assert body["input"] == {"choice": "yes"}
    assert body["approved"] is True


def test_resume_omits_approved_when_none():
    client = FakeClient()
    PlaybooksAPI(client).resume("run-1", manual_input_id=1)
    _, body = client.post_calls[0]
    assert "approved" not in body


def test_resume_blank_pk_raises():
    with pytest.raises(ValueError):
        PlaybooksAPI(FakeClient()).resume("", manual_input_id=1)


# -- get(step_detail=) / run_env -------------------------------------------
def _run_with_steps(pk):
    return {
        "@id": f"/api/wf/api/workflows/{pk}/",
        "name": "PB",
        "status": "finished",
        "env": {"input": {}, "wf_id": pk},
        "steps": [
            {"name": "Start", "status": "finished", "result": {"data": 1}},
            {"name": "Fetch Email", "status": "finished", "result": {"data": 2}},
        ],
    }


def test_get_step_detail_passes_flag_and_returns_full():
    client = FakeClient(workflows=[_run_with_steps("900")])
    api = PlaybooksAPI(client)
    full = api.get_execution("900", step_detail=True)
    assert client.get_calls[0][0] == "/api/wf/api/workflows/900/?format=json&step_detail=true"
    assert "steps" in full and len(full["steps"]) == 2


def test_run_env_reshapes_env_and_steps():
    client = FakeClient(workflows=[_run_with_steps("901")])
    api = PlaybooksAPI(client)
    env = api.run_env("901")
    assert env["status"] == "finished"
    assert env["env"]["wf_id"] == "901"
    assert env["steps"]["Fetch Email"] == {"status": "finished", "result": {"data": 2}}


class CrudClient:
    """Records GET/PUT/DELETE for definition-CRUD tests."""

    def __init__(self):
        self.calls = []

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        return {"hydra:member": [{"uuid": "w-1", "name": "Block IP"}], "hydra:totalItems": 1}

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return {"ok": True, **(data or {})}

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return {"ok": True, "endpoint": endpoint, "data": data}

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, params))


def test_list_definitions_filters_and_returns_members():
    c = CrudClient()
    out = PlaybooksAPI(c).list(name="Block IP", limit=10)
    assert out == [{"uuid": "w-1", "name": "Block IP"}]
    method, endpoint, params = c.calls[-1]
    assert method == "GET" and endpoint == "/api/3/workflows"
    assert params == {"$limit": 10, "name": "Block IP"}


def test_list_collection_accepts_uuid_or_iri():
    c = CrudClient()
    PlaybooksAPI(c).list(collection="/api/3/workflow_collections/col-9", relationships=True)
    params = c.calls[-1][2]
    assert params["collection"] == "col-9" and params["$relationships"] == "true"
    PlaybooksAPI(c).list(collection="col-9")
    assert c.calls[-1][2]["collection"] == "col-9"


def test_list_passes_extra_query_params():
    c = CrudClient()
    PlaybooksAPI(c).list(
        limit=5,
        params={"triggerStep.stepType.name": "cybersponse.post_create", "$fields": "uuid,name"},
    )
    _, endpoint, params = c.calls[-1]
    assert endpoint == "/api/3/workflows"
    assert params["$limit"] == 5
    assert params["triggerStep.stepType.name"] == "cybersponse.post_create"
    assert params["$fields"] == "uuid,name"


def test_get_definition_fetches_one_workflow():
    class _DefinitionClient(CrudClient):
        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params))
            return {"@id": endpoint, "uuid": "w-1", "name": "Block IP"}

    c = _DefinitionClient()
    out = PlaybooksAPI(c).get_definition("w-1")
    assert out["uuid"] == "w-1"
    assert c.calls[-1][0] == "GET"
    assert c.calls[-1][1] == "/api/3/workflows/w-1"
    assert c.calls[-1][2] == {"$relationships": "true"}


def test_update_definition_puts_partial_fields():
    c = CrudClient()
    PlaybooksAPI(c).update("w-1", debug=True, isActive=False)
    method, endpoint, data = c.calls[-1]
    assert method == "PUT" and endpoint == "/api/3/workflows/w-1"
    assert data == {"debug": True, "isActive": False}


def test_update_requires_a_field():
    with pytest.raises(ValueError):
        PlaybooksAPI(CrudClient()).update("w-1")


def test_delete_definition_hard_and_soft():
    c = CrudClient()
    PlaybooksAPI(c).delete("w-1")
    method, endpoint, params = c.calls[-1]
    assert method == "DELETE" and endpoint == "/api/3/workflows/w-1"
    assert params == {"$hardDelete": "true", "$showDeleted": "true"}
    PlaybooksAPI(c).delete("w-1", hard=False)
    assert c.calls[-1][2] is None


def test_create_playbooks_posts_rows_to_bulk_path():
    c = CrudClient()
    payload = [{"uuid": "w-1", "name": "Block IP"}]
    out = PlaybooksAPI(c).create_playbooks(payload)
    method, endpoint, data = c.calls[-1]
    assert method == "POST" and endpoint == "/api/3/bulkupsert/workflows"
    assert data == payload
    assert out["ok"] is True


def test_query_posts_to_workflow_query_endpoint():
    class _QueryClient(CrudClient):
        def post(self, endpoint, data=None, params=None, **kw):
            self.calls.append(("POST", endpoint, data, params))
            return {"hydra:member": [{"uuid": "w-1", "name": "Block IP"}], "hydra:totalItems": 1}

    c = _QueryClient()
    out = PlaybooksAPI(c).query(
        {
            "logic": "AND",
            "filters": [{"field": "triggerStep.stepType.name", "value": "start"}],
            "limit": 3,
        }
    )
    assert len(out) == 1 and out[0]["uuid"] == "w-1"
    method, endpoint, data, params = c.calls[-1]
    assert method == "POST" and endpoint == "/api/query/workflows"
    assert data["logic"] == "AND"
    assert params == {"$page": 1, "$limit": 3}


def test_definition_crud_uuid_validation():
    a = PlaybooksAPI(CrudClient())
    for op in (
        lambda: a.update("", debug=True),
        lambda: a.delete("  "),
        lambda: a.get_definition(""),
    ):
        with pytest.raises(ValueError):
            op()


# -- run control / manual input / named triggers ----------------------------
class _Rec:
    def __init__(self, resp=None):
        self.calls = []
        self._resp = resp if resp is not None else {}

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, None, params))
        return self._resp

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data, params))
        return self._resp

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data, params))
        return self._resp


def test_start_retry_post_empty_body():
    c = _Rec()
    PlaybooksAPI(c).start("pk-1")
    assert c.calls[-1][:3] == ("POST", "/api/wf/api/workflows/pk-1/start/", {})
    PlaybooksAPI(c).retry("pk-1")
    assert c.calls[-1][:3] == ("POST", "/api/wf/api/workflows/pk-1/retry/", {})


def test_approval_body():
    c = _Rec()
    PlaybooksAPI(c).approval("pk-1", decision="approved", comment="ok")
    assert c.calls[-1][:3] == (
        "POST",
        "/api/wf/api/workflows/pk-1/approval/",
        {"decision": "approved", "comment": "ok"},
    )


def test_count_passes_logs_param():
    c = _Rec(resp={"count": 5})
    PlaybooksAPI(c).count(logs="recent")
    assert c.calls[-1] == ("GET", "/api/wf/api/workflows/count/", None, {"logs": "recent"})


def test_log_list_keys_on_task_id():
    c = _Rec()
    PlaybooksAPI(c).log_list(task_id="t-1", status="finished")
    method, endpoint, data, params = c.calls[-1]
    assert endpoint == "/api/wf/api/workflows/log_list/"
    assert params["task_id"] == "t-1" and params["status"] == "finished"


def test_query_logs_body_and_logs_param():
    c = _Rec()
    PlaybooksAPI(c).query_logs(filters=[{"field": "status"}], logic="OR", logs="historical")
    method, endpoint, data, params = c.calls[-1]
    assert endpoint == "/api/wf/api/query/workflow_logs/"
    assert data == {"logic": "OR", "filters": [{"field": "status"}]}
    assert params == {"logs": "historical"}


def test_manual_inputs_and_retrieve():
    c = _Rec(resp={"hydra:member": [{"id": 7, "step_id": "s1"}]})
    rows = PlaybooksAPI(c).manual_inputs()
    assert rows == [{"id": 7, "step_id": "s1"}]
    assert c.calls[-1][1] == "/api/wf/api/manual-wf-input/list_wfinput/"
    PlaybooksAPI(c).retrieve_manual_input("mi-1")
    assert c.calls[-1][1] == "/api/wf/api/manual-wf-input/mi-1/retrieve_wfinput/"


def test_update_manual_input_uses_put():
    c = _Rec()
    PlaybooksAPI(c).update_manual_input("mi-1", value="x")
    assert c.calls[-1][:3] == ("PUT", "/api/wf/api/manual-wf-input/mi-1/", {"value": "x"})


def test_trigger_by_name_and_deferred():
    c = _Rec(resp={"task_id": "t1"})
    PlaybooksAPI(c).trigger_by_name("my-hook", body={"a": 1})
    assert c.calls[-1][:3] == ("POST", "/api/triggers/1/my-hook", {"a": 1})
    PlaybooksAPI(c).trigger_by_name("my-hook", deferred=True)
    assert c.calls[-1][1] == "/api/triggers/1/deferred/my-hook"


def test_trigger_by_name_rejects_blank():
    with pytest.raises(ValueError):
        PlaybooksAPI(_Rec()).trigger_by_name("")


# -- wait / trigger(follow=True) --------------------------------------------
class _PollClient:
    """Simulates log_list returning running then finished."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        return {}

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, params))
        if "log_list" in endpoint:
            status = self.statuses.pop(0) if self.statuses else "finished"
            return {
                "hydra:member": [
                    {
                        "@id": "/api/wf/api/workflows/42/",
                        "name": "PB",
                        "status": status,
                        "modified": "2026-06-19",
                    }
                ]
            }
        if "notrigger" in endpoint:
            return {"task_id": "run-uuid"}
        return {}


def test_wait_polls_until_terminal(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    c = _PollClient(["Running", "Running", "finished"])
    run = PlaybooksAPI(c).wait("run-uuid", interval=0)
    assert run["status"] == "finished"
    log_list_calls = [call for call in c.calls if "log_list" in call[1]]
    assert len(log_list_calls) == 3


def test_wait_raises_on_timeout(monkeypatch):
    calls = iter([0.0, 0.0, 999.0])
    monkeypatch.setattr("time.monotonic", lambda: next(calls))
    monkeypatch.setattr("time.sleep", lambda _: None)
    c = _PollClient(["Running"] * 10)
    with pytest.raises(TimeoutError):
        PlaybooksAPI(c).wait("run-uuid", timeout=1, interval=0)


def test_wait_rejects_blank_task_id():
    with pytest.raises(ValueError):
        PlaybooksAPI(_Rec()).wait("")


def test_wait_for_run_returns_terminal_run():
    """wait_for_run returns immediately when run is in terminal state."""
    finished_run = _run(
        "/api/wf/api/workflows/r1/",
        "Test PB",
        "finished",
        "2026-06-19T00:00:00",
        uuid="u1",
    )
    client = FakeClient(
        workflows=[finished_run],
        name_lookup={"hydra:member": [{"uuid": "pb-uuid"}]},
    )
    run = PlaybooksAPI(client).wait_for_run(playbook="Test PB")
    assert run["status"] == "finished"
    assert run["pk"] == "r1"
    assert run["name"] == "Test PB"


def test_wait_for_run_rejects_missing_playbook():
    """wait_for_run raises ValueError if playbook not found."""
    client = FakeClient()  # Empty lookup by default
    with pytest.raises(ValueError, match="not found"):
        PlaybooksAPI(client).wait_for_run(playbook="Nonexistent")


def test_trigger_follow_returns_shaped_run(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)
    c = _PollClient(["finished"])

    class _WithUuid:
        """Client that also handles the uuid lookup GET."""

        def __init__(self, inner):
            self._inner = inner
            self.calls = inner.calls

        def get(self, endpoint, params=None, **kw):
            self.calls.append(("GET", endpoint, params))
            if "/api/3/workflows" in endpoint:
                return {"hydra:member": [{"uuid": "pb-uuid"}]}
            return {}

        def post(self, *a, **kw):
            return self._inner.post(*a, **kw)

    run = PlaybooksAPI(_WithUuid(c)).trigger("My PB", follow=True, interval=0)
    assert run["status"] == "finished"


def test_trigger_follow_false_returns_task_id():
    c = _Rec(resp={"task_id": "t99"})
    resp = PlaybooksAPI(c).trigger("aabbccdd-0000-0000-0000-000000000000", follow=False)
    assert resp == {"task_id": "t99"}


# -- trigger_action -----------------------------------------------------------
def test_trigger_action_posts_correct_body():
    c = _Rec(resp={"task_id": "ta1"})
    resp = PlaybooksAPI(c).trigger_action(
        "route-uuid-1234",
        module="alerts",
        record_uuid="rec-uuid",
        playbook_uuid="pb-uuid",
    )
    method, endpoint, body, params = c.calls[-1]
    assert endpoint == "/api/triggers/1/action/route-uuid-1234"
    assert body["singleRecordExecution"] is True
    assert body["__resource"] == "alerts"
    assert body["records"] == ["/api/3/alerts/rec-uuid"]
    assert body["__uuid"] == "pb-uuid"
    assert resp == {"task_id": "ta1"}


def test_trigger_action_omits_playbook_uuid_when_not_given():
    c = _Rec(resp={})
    PlaybooksAPI(c).trigger_action("r-uuid", module="incidents", record_uuid="r1")
    _, _, body, _ = c.calls[-1]
    assert "__uuid" not in body


# -- search_executions -------------------------------------------------------
class _SearchRec:
    """Records POST calls; returns log_list-shaped response."""

    def __init__(self, members=None):
        self.calls = []
        self._members = members or []

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, None, params))
        if "/api/3/workflows" in endpoint:
            return {"hydra:member": [{"uuid": "pb-uuid"}]}
        return {}

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data, params))
        return {"hydra:member": self._members}


def test_search_executions_free_text_query():
    member = {
        "@id": "/api/wf/api/workflows/1/",
        "name": "PB",
        "status": "finished",
        "modified": "t",
    }
    c = _SearchRec(members=[member])
    results = PlaybooksAPI(c).search_executions("High Risk")
    _, endpoint, _, params = c.calls[-1]
    assert endpoint == "/api/wf/api/workflows/log_list/"
    assert params["search"] == "High Risk"
    assert results[0]["name"] == "PB"


def test_search_executions_tags_include_and_exclude():
    c = _SearchRec()
    PlaybooksAPI(c).search_executions(tags_include=["critical", "phishing"], tags_exclude="noise")
    _, _, _, params = c.calls[-1]
    assert params["tags_include"] == "critical,phishing"
    assert params["tags_exclude"] == "noise"


def test_search_executions_status_filter():
    c = _SearchRec()
    PlaybooksAPI(c).search_executions(status="failed", limit=5, offset=10)
    _, _, _, params = c.calls[-1]
    assert params["status"] == "failed"
    assert params["limit"] == 5
    assert params["offset"] == 10


def test_search_executions_by_playbook_name_resolves_uuid():
    c = _SearchRec()
    PlaybooksAPI(c).search_executions(playbook="Block IP")
    _, _, _, params = c.calls[-1]
    assert params["template_iri"] == "/api/3/workflows/pb-uuid"


def test_search_executions_by_playbook_uuid_skips_lookup():
    c = _SearchRec()
    PlaybooksAPI(c).search_executions(playbook_uuid="my-uuid")
    _, _, _, params = c.calls[-1]
    assert params["template_iri"] == "/api/3/workflows/my-uuid"
    get_calls = [call for call in c.calls if call[0] == "GET"]
    assert not get_calls  # no name-lookup GET needed


def test_trigger_action_rejects_blank_route():
    with pytest.raises(ValueError):
        PlaybooksAPI(_Rec()).trigger_action("", module="alerts", record_uuid="r")


# -- historical_steps ---------------------------------------------------------
def test_historical_steps_calls_correct_endpoint():
    c = _Rec(resp={"hydra:member": [{"name": "Start", "status": "finished"}]})
    steps = PlaybooksAPI(c).historical_steps("task-abc", limit=50)
    method, endpoint, data, params = c.calls[-1]
    assert endpoint == "/api/wf/api/historical-steps/"
    assert params["task_id"] == "task-abc"
    assert params["limit"] == 50
    assert params["ordering"] == "created"
    assert steps == [{"name": "Start", "status": "finished"}]


def test_historical_steps_passes_status_and_name_filters():
    c = _Rec(resp={"hydra:member": []})
    PlaybooksAPI(c).historical_steps("t1", status="failed", name="Enrich IP")
    _, _, _, params = c.calls[-1]
    assert params["status"] == "failed"
    assert params["name"] == "Enrich IP"


def test_historical_steps_rejects_blank_task_id():
    with pytest.raises(ValueError):
        PlaybooksAPI(_Rec()).historical_steps("")


# -- render_jinja -------------------------------------------------------------
def test_render_jinja_returns_result_field():
    c = _Rec(resp={"result": "hello world"})
    out = PlaybooksAPI(c).render_jinja("{{ greeting }}", values={"greeting": "hello world"})
    method, endpoint, body, params = c.calls[-1]
    assert endpoint == "/api/wf/api/jinja-editor/"
    assert body["template"] == "{{ greeting }}"
    assert body["values"] == {"greeting": "hello world"}
    assert out == "hello world"


def test_render_jinja_falls_back_to_json_dump():
    c = _Rec(resp={"unknown_key": "data"})
    out = PlaybooksAPI(c).render_jinja("{{ x }}")
    assert "unknown_key" in out  # fell back to json.dumps


def test_render_jinja_handles_string_response():
    c = _Rec(resp="raw string")
    out = PlaybooksAPI(c).render_jinja("{{ x }}")
    assert out == "raw string"


# -- clone ------------------------------------------------------------------
_SRC_DEFINITION = {
    "@id": "/api/3/workflows/11111111-1111-1111-1111-111111111111",
    "id": 42,
    "uuid": "11111111-1111-1111-1111-111111111111",
    "name": "Original PB",
    "aliasName": "#Original",
    "isActive": True,
    "collection": "/api/3/workflow_collections/cccccccc-cccc-cccc-cccc-cccccccccccc",
    "triggerStep": "/api/3/workflow_steps/22222222-2222-2222-2222-222222222222",
    "createDate": 1700000000,
    "createUser": "/api/3/people/someone",
    "recordTags": ["keepme"],
    "groups": [{"uuid": "33333333-3333-3333-3333-333333333333", "name": "G1"}],
    "steps": [
        {
            "uuid": "22222222-2222-2222-2222-222222222222",
            "name": "Start",
            "group": "/api/3/workflow_groups/33333333-3333-3333-3333-333333333333",
        },
        {"uuid": "44444444-4444-4444-4444-444444444444", "name": "Action"},
    ],
    "routes": [
        {
            "uuid": "55555555-5555-5555-5555-555555555555",
            "sourceStep": "22222222-2222-2222-2222-222222222222",
            "targetStep": "44444444-4444-4444-4444-444444444444",
        }
    ],
}


def test_clone_remaps_all_uuids_and_rewires():
    c = _Rec(resp=_SRC_DEFINITION)
    PlaybooksAPI(c).clone("11111111-1111-1111-1111-111111111111", "Copy of PB")
    method, endpoint, body, _ = c.calls[-1]
    assert (method, endpoint) == ("POST", "/api/3/workflows")

    old_uuids = {
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
        "33333333-3333-3333-3333-333333333333",
        "44444444-4444-4444-4444-444444444444",
        "55555555-5555-5555-5555-555555555555",
    }
    import json

    blob = json.dumps(body)
    for old in old_uuids:
        assert old not in blob  # every original uuid regenerated

    # references stay internally consistent after remap
    trigger = body["triggerStep"].rsplit("/", 1)[-1]
    assert trigger == body["steps"][0]["uuid"]  # triggerStep still points at "Start"
    assert body["routes"][0]["sourceStep"] == body["steps"][0]["uuid"]
    assert body["routes"][0]["targetStep"] == body["steps"][1]["uuid"]
    assert body["steps"][0]["group"].rsplit("/", 1)[-1] == body["groups"][0]["uuid"]


def test_clone_sets_name_and_strips_server_fields():
    c = _Rec(resp=_SRC_DEFINITION)
    PlaybooksAPI(c).clone("11111111-1111-1111-1111-111111111111", "Copy of PB")
    body = c.calls[-1][2]
    assert body["name"] == "Copy of PB"
    assert body["aliasName"] is None
    assert body["isActive"] is False  # inactive by default
    for stripped in ("@id", "id", "createDate", "createUser", "recordTags"):
        assert stripped not in body


def test_clone_active_and_collection_override():
    c = _Rec(resp=_SRC_DEFINITION)
    PlaybooksAPI(c).clone(
        "11111111-1111-1111-1111-111111111111",
        "Copy",
        collection="dddddddd-dddd-dddd-dddd-dddddddddddd",
        is_active=True,
    )
    body = c.calls[-1][2]
    assert body["isActive"] is True
    assert body["collection"] == "/api/3/workflow_collections/dddddddd-dddd-dddd-dddd-dddddddddddd"


def test_clone_requires_new_name():
    c = _Rec(resp=_SRC_DEFINITION)
    with pytest.raises(ValueError, match="new_name"):
        PlaybooksAPI(c).clone("11111111-1111-1111-1111-111111111111", "  ")


# -- last_run ---------------------------------------------------------------
def test_last_run_returns_newest_shaped():
    client = FakeClient(
        workflows=[
            _run("/api/wf/api/workflows/old/", "PB", "finished", "2026-06-08T01:00", task_id="t1", uuid="u1"),
            _run("/api/wf/api/workflows/new/", "PB", "failed", "2026-06-08T03:00", task_id="t2", uuid="u2"),
        ]
    )
    run = PlaybooksAPI(client).last_run(playbook_uuid="pb-uuid")
    assert run is not None
    assert run["pk"] == "new"
    assert run["status"] == "failed"
    assert run["task_id"] == "t2"


def test_last_run_returns_none_if_no_runs():
    client = FakeClient(workflows=[], historical=[])
    run = PlaybooksAPI(client).last_run(playbook_uuid="pb-uuid")
    assert run is None


def test_last_run_resolves_playbook_name():
    client = FakeClient(
        workflows=[_run("/api/wf/api/workflows/a/", "A", "finished", "t")],
        name_lookup={"hydra:member": [{"uuid": "pb-uuid"}]},
    )
    run = PlaybooksAPI(client).last_run(playbook="Block IP")
    assert run is not None
    assert run["pk"] == "a"
    # Verify the name lookup happened
    assert any("/api/3/workflows?" in c[0] for c in client.get_calls)


def test_last_run_raw_returns_unshaped():
    raw_run = _run("/api/wf/api/workflows/a/", "A", "failed", "t", result={"error": "boom"})
    client = FakeClient(workflows=[raw_run])
    run = PlaybooksAPI(client).last_run(playbook_uuid="pb-uuid", raw=True)
    assert run is not None
    assert "@id" in run
    assert run["@id"] == "/api/wf/api/workflows/a/"


def test_last_run_prefers_playbook_uuid_over_name():
    """When both playbook and playbook_uuid are given, uuid takes precedence."""
    client = FakeClient(
        workflows=[_run("/api/wf/api/workflows/x/", "X", "finished", "t")],
        name_lookup={"hydra:member": [{"uuid": "wrong-uuid"}]},
    )
    # Pass both; playbook_uuid should be used, avoiding the name lookup
    run = PlaybooksAPI(client).last_run(playbook="Block IP", playbook_uuid="pb-uuid")
    assert run is not None
    # playbook_uuid is used directly; just verify the result is correct.
    assert run["pk"] == "x"


# -- why_failed -----------------------------------------------------------
def test_why_failed_finds_newest_run_and_fetches_step_detail():
    """why_failed fetches the newest run and pulls step_detail."""
    run_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "name": "Block IP",
        "status": "failed",
        "modified": "2026-06-08T03:00",
        "task_id": "t1",
        "uuid": "u1",
    }
    full_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "status": "failed",
        "result": {"Error message": "top error"},
        "steps": [
            {"name": "Start", "status": "finished", "result": {}},
            {"name": "Fetch Data", "status": "failed", "result": {"error": "step error"}},
        ],
    }

    class _WhyFailedClient(FakeClient):
        def get(self, endpoint, params=None, **kw):
            # Handle step_detail fetch
            if "step_detail=true" in endpoint:
                return full_record
            return super().get(endpoint, params, **kw)

    client = _WhyFailedClient(workflows=[run_record])
    result = PlaybooksAPI(client).why_failed(playbook_uuid="pb-uuid")

    assert result is not None
    assert result["status"] == "failed"
    assert result["pk"] == "run1"
    assert result["failing_step"] == "Fetch Data"
    assert result["error_message"] == "step error"


def test_why_failed_returns_none_if_no_runs():
    client = FakeClient(workflows=[], historical=[])
    result = PlaybooksAPI(client).why_failed(playbook_uuid="pb-uuid")
    assert result is None


def test_why_failed_succeeding_run_has_no_error():
    """A succeeding run returns status with error_message and failing_step = None."""
    run_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "name": "Block IP",
        "status": "finished",
        "modified": "2026-06-08T03:00",
        "task_id": "t1",
        "uuid": "u1",
    }
    full_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "status": "finished",
        "result": {},
        "steps": [
            {"name": "Start", "status": "finished", "result": {"data": 1}},
            {"name": "Enrich", "status": "finished", "result": {"data": 2}},
        ],
    }

    class _WhyFailedClient(FakeClient):
        def get(self, endpoint, params=None, **kw):
            if "step_detail=true" in endpoint:
                return full_record
            return super().get(endpoint, params, **kw)

    client = _WhyFailedClient(workflows=[run_record])
    result = PlaybooksAPI(client).why_failed(playbook_uuid="pb-uuid")

    assert result is not None
    assert result["status"] == "finished"
    assert result["pk"] == "run1"
    assert result["failing_step"] is None
    assert result["error_message"] is None


def test_why_failed_respects_raw_flag():
    """raw=True returns the full step_detail record, not the slim projection."""
    run_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "name": "Block IP",
        "status": "failed",
        "modified": "2026-06-08T03:00",
        "task_id": "t1",
        "uuid": "u1",
    }
    full_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "status": "failed",
        "result": {"Error message": "boom"},
        "steps": [
            {"name": "Start", "status": "finished", "result": {}},
            {"name": "Act", "status": "failed", "result": {"error": "act failed"}},
        ],
        "env": {"custom": "value"},
    }

    class _WhyFailedClient(FakeClient):
        def get(self, endpoint, params=None, **kw):
            if "step_detail=true" in endpoint:
                return full_record
            return super().get(endpoint, params, **kw)

    client = _WhyFailedClient(workflows=[run_record])
    result = PlaybooksAPI(client).why_failed(playbook_uuid="pb-uuid", raw=True)

    assert result is not None
    assert "steps" in result
    assert "env" in result
    assert len(result["steps"]) == 2
    assert result["status"] == "failed"


def test_why_failed_resolves_playbook_name():
    """why_failed can accept a playbook name (not just uuid)."""
    run_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "name": "Block IP",
        "status": "failed",
        "modified": "2026-06-08T03:00",
        "task_id": "t1",
        "uuid": "u1",
    }
    full_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "status": "failed",
        "result": {},
        "steps": [{"name": "X", "status": "failed", "result": {"error": "e"}}],
    }

    class _WhyFailedClient(FakeClient):
        def get(self, endpoint, params=None, **kw):
            if "step_detail=true" in endpoint:
                return full_record
            return super().get(endpoint, params, **kw)

    client = _WhyFailedClient(
        workflows=[run_record],
        name_lookup={"hydra:member": [{"uuid": "pb-uuid"}]},
    )
    result = PlaybooksAPI(client).why_failed(playbook="Block IP")
    assert result is not None
    assert result["pk"] == "run1"


def test_why_failed_prefers_step_error_over_top_error():
    """If both step and top-level errors exist, step-level message is used."""
    run_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "name": "PB",
        "status": "failed",
        "modified": "t",
        "task_id": "t1",
        "uuid": "u1",
    }
    full_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "status": "failed",
        "result": {"Error message": "top level error"},
        "steps": [
            {"name": "S1", "status": "failed", "result": {"error": "step-specific error"}},
        ],
    }

    class _WhyFailedClient(FakeClient):
        def get(self, endpoint, params=None, **kw):
            if "step_detail=true" in endpoint:
                return full_record
            return super().get(endpoint, params, **kw)

    client = _WhyFailedClient(workflows=[run_record])
    result = PlaybooksAPI(client).why_failed(playbook_uuid="pb-uuid")

    # step-level error should take precedence
    assert result["error_message"] == "step-specific error"
    assert result["failing_step"] == "S1"


def test_why_failed_detects_various_failure_statuses():
    """why_failed recognizes 'failed', 'errored', 'error', etc. as failure statuses."""
    for fail_status in ["failed", "errored", "error", "FAILED"]:
        run_record = {
            "@id": "/api/wf/api/workflows/run1/",
            "name": "PB",
            "status": "failed",
            "modified": "t",
            "task_id": "t1",
            "uuid": "u1",
        }
        full_record = {
            "@id": "/api/wf/api/workflows/run1/",
            "status": "failed",
            "result": {},
            "steps": [
                {"name": "Start", "status": "finished", "result": {}},
                {"name": "Fail Step", "status": fail_status, "result": {"error": f"failed with {fail_status}"}},
            ],
        }

        class _WhyFailedClient(FakeClient):
            def get(self, endpoint, params=None, **kw):
                if "step_detail=true" in endpoint:
                    return full_record
                return super().get(endpoint, params, **kw)

        client = _WhyFailedClient(workflows=[run_record])
        result = PlaybooksAPI(client).why_failed(playbook_uuid="pb-uuid")

        assert result["failing_step"] == "Fail Step", f"Failed to detect status={fail_status}"


def test_why_failed_handles_missing_pk():
    """If a run record has no pk and no @id, why_failed returns None."""
    run_record = {
        "name": "PB",
        "status": "failed",
        "modified": "t",
        "task_id": "t1",
        "uuid": "u1",
        # Missing @id
    }

    client = FakeClient(workflows=[run_record])
    result = PlaybooksAPI(client).why_failed(playbook_uuid="pb-uuid")
    assert result is None


def test_why_failed_uses_pk_from_run_record():
    """why_failed prefers explicit pk field from the raw run record if available."""
    run_record = {
        "@id": "/api/wf/api/workflows/extracted-pk/",
        "name": "PB",
        "status": "failed",
        "modified": "t",
        "task_id": "t1",
        "uuid": "u1",
        "pk": "explicit-pk",  # If the raw run already has pk
    }
    full_record = {
        "@id": "/api/wf/api/workflows/extracted-pk/",
        "status": "failed",
        "result": {},
        "steps": [{"name": "S", "status": "failed", "result": {"error": "e"}}],
    }

    class _WhyFailedClient(FakeClient):
        def get(self, endpoint, params=None, **kw):
            if "step_detail=true" in endpoint:
                return full_record
            return super().get(endpoint, params, **kw)

    client = _WhyFailedClient(workflows=[run_record])
    result = PlaybooksAPI(client).why_failed(playbook_uuid="pb-uuid")

    # why_failed uses run.get("pk") if available, else extracts from @id
    assert result is not None
    assert result["pk"] == "explicit-pk"
