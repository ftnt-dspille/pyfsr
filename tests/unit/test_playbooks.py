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
    runs = PlaybooksAPI(client).runs(limit=10)
    pks = [r["pk"] for r in runs]
    assert pks == ["a", "dup", "b"]  # sorted by modified desc, deduped
    assert {r["source"] for r in runs} == {"live", "historical"}


def test_runs_respects_limit():
    wf = [_run(f"/api/wf/api/workflows/{i}/", f"n{i}", "finished", f"t{i}") for i in range(5)]
    runs = PlaybooksAPI(FakeClient(workflows=wf)).runs(limit=2)
    assert len(runs) == 2


def test_runs_by_playbook_name_resolves_uuid():
    client = FakeClient(
        workflows=[_run("/api/wf/api/workflows/a/", "A", "failed", "t")],
        name_lookup={"hydra:member": [{"uuid": "pb-uuid"}]},
    )
    PlaybooksAPI(client).runs(playbook="Block IP")
    # name lookup happened, and the run fetch carried the template_iri filter
    assert any("/api/3/workflows?" in c[0] for c in client.get_calls)
    assert any("template_iri=/api/3/workflows/pb-uuid" in c[0] for c in client.get_calls)


def test_runs_unknown_playbook_returns_empty():
    client = FakeClient(name_lookup={"hydra:member": []})
    assert PlaybooksAPI(client).runs(playbook="nope") == []


def test_runs_raw_returns_unshaped():
    client = FakeClient(workflows=[_run("/api/wf/api/workflows/a/", "A", "failed", "t")])
    runs = PlaybooksAPI(client).runs(raw=True)
    assert "@id" in runs[0]


# -- get --------------------------------------------------------------------
def test_get_live_then_historical_fallback():
    client = FakeClient(
        workflows=[],
        historical=[_run("/api/wf/api/historical-workflows/h1/", "H", "failed", "t", uuid="u")],
    )
    run = PlaybooksAPI(client).get("h1")
    assert run["pk"] == "h1"
    assert run["source"] == "historical"


def test_get_blank_pk_raises():
    with pytest.raises(ValueError):
        PlaybooksAPI(FakeClient()).get("")


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
    PlaybooksAPI(client).resume(
        "run-1", manual_input_id=7, input={"choice": "yes"}, step_id="s1", approved=True
    )
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
    full = api.get("900", step_detail=True)
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


def test_definition_crud_uuid_validation():
    a = PlaybooksAPI(CrudClient())
    for op in (lambda: a.update("", debug=True), lambda: a.delete("  ")):
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
