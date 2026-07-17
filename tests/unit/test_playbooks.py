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


def test_resolve_iri_returns_workflow_iri_for_name():
    client = FakeClient(name_lookup={"hydra:member": [{"uuid": "pb-uuid"}]})
    assert PlaybooksAPI(client).resolve_iri("Nightly Recon") == "/api/3/workflows/pb-uuid"


def test_resolve_iri_returns_none_when_playbook_absent():
    client = FakeClient(name_lookup={"hydra:member": []})
    assert PlaybooksAPI(client).resolve_iri("Nope") is None


def test_runs_preserve_full_record_in_extra():
    # No raw/typed toggle: the typed RunSummary carries the full wire record in
    # extra, so unshaped fields like @id stay reachable by item access.
    client = FakeClient(workflows=[_run("/api/wf/api/workflows/a/", "A", "failed", "t")])
    runs = PlaybooksAPI(client).execution_history()
    assert "@id" in runs[0]
    assert runs[0]["@id"] == "/api/wf/api/workflows/a/"
    assert runs[0].pk == "a"


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
        inputs={"foo": "bar"},
    )
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/triggers/1/notrigger/f0674018-306d-414a-94da-90ace5d98350"
    assert body["inputs"] == {"foo": "bar"}
    assert out["task_id"] == "run-uuid"


def test_trigger_rejects_records_and_points_at_trigger_action():
    """`records=` on the notrigger route starts a record-BLIND run — fail loudly.

    Live-verified: the notrigger route leaves `vars.input.records` empty no matter
    what body it is given (records alone, or the full action envelope), so a step
    reading `{{ vars.input.records[0]['@id'] }}` dies with CS-WF-35 while the
    trigger call itself looks successful. Silently starting that run is worse than
    refusing it.
    """
    client = FakeClient()
    with pytest.raises(ValueError, match="trigger_action"):
        PlaybooksAPI(client).trigger("f0674018-306d-414a-94da-90ace5d98350", records="alerts:abc-123")
    assert not client.post_calls, "must refuse BEFORE starting a record-blind run"


def test_trigger_by_name_resolves_uuid_first():
    client = FakeClient(name_lookup={"hydra:member": [{"uuid": "pb-uuid"}]})
    PlaybooksAPI(client).trigger("AI Investigation")
    assert any("/api/3/workflows?" in c[0] for c in client.get_calls)
    assert client.post_calls[0][0] == "/api/triggers/1/notrigger/pb-uuid"


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
    step = env["steps"]["Fetch Email"]
    assert step["status"] == "finished"
    assert step["result"] == {"data": 2}


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


# ----------------------------------------------------------------- find() helpers
def test_find_maps_friendly_step_and_trigger_types():
    c = CrudClient()
    PlaybooksAPI(c).find_with_step_type("connector", limit=10)
    p = c.calls[-1][2]
    assert p["steps.stepType.name"] == "Connectors" and p["$limit"] == 10
    PlaybooksAPI(c).find_by_trigger_type("api_endpoint")
    assert c.calls[-1][2]["triggerStep.stepType.name"] == "cybersponse.api_call"
    # friendly aliases that differ from the compiler's emit names
    PlaybooksAPI(c).find(step_type="approval")
    assert c.calls[-1][2]["steps.stepType.name"] == "ApprovalManualInput"


def test_find_passes_raw_step_type_through():
    c = CrudClient()
    PlaybooksAPI(c).find(step_type="CyopsUtilites", trigger_type="cybersponse.action")
    p = c.calls[-1][2]
    assert p["steps.stepType.name"] == "CyopsUtilites"
    assert p["triggerStep.stepType.name"] == "cybersponse.action"


def test_find_booleans_and_substrings():
    c = CrudClient()
    PlaybooksAPI(c).find(active=True, private=False, name_contains="phish", tag="ioc")
    p = c.calls[-1][2]
    assert p["isActive"] == "true" and p["isPrivate"] == "false"
    assert p["name$like"] == "%phish%" and p["tag$like"] == "%ioc%"


def test_find_using_connector_and_operation():
    c = CrudClient()
    PlaybooksAPI(c).find_using_connector("fortigate")
    assert c.calls[-1][2]["steps.arguments$like"] == "%fortigate%"
    PlaybooksAPI(c).find_using_connector("fortigate", operation="block_ip")
    assert c.calls[-1][2]["steps.arguments$like"] == "%block_ip%"


def test_find_route_implies_api_endpoint_trigger():
    c = CrudClient()
    PlaybooksAPI(c).find_by_route("lookup_ip")
    p = c.calls[-1][2]
    assert p["steps.arguments$like"] == "%lookup_ip%"
    assert p["triggerStep.stepType.name"] == "cybersponse.api_call"


def test_find_referencing_implies_reference_step():
    c = CrudClient()
    PlaybooksAPI(c).find_referencing("Enrich IP")
    p = c.calls[-1][2]
    assert p["steps.arguments$like"] == "%Enrich IP%"
    assert p["steps.stepType.name"] == "WorkflowReference"


def test_find_rejects_multiple_argument_substring_filters():
    c = CrudClient()
    with pytest.raises(ValueError, match="only one of uses_connector"):
        PlaybooksAPI(c).find(uses_connector="fortigate", route="x")


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
    # pb.approval now resolves the run's pending manual-wf-input and resumes via
    # wfinput_resume (the canonical path), NOT the old /approval/ endpoint (which
    # only lists approvals, never resumes). See MANUAL_INPUT.md + the
    # manual-input-run-scoped-helper memory.
    from pyfsr.api.manual_input import ManualInputAPI

    class _ApprovalClient:
        """Scripts the approval-resume sequence: GET ?workflow=, retrieve_wfinput,
        wfinput_resume, + /api/3/people for user resolution."""

        def __init__(self):
            self.manual_input = ManualInputAPI(self)
            self.posts = []
            self.gets = []

        def get(self, endpoint, params=None, **kw):
            self.gets.append((endpoint, params))
            if endpoint == "/api/3/people":
                return {"hydra:member": [{"@id": "/api/3/people/admin", "firstname": "CS", "lastname": "Admin"}]}
            if endpoint == "/api/wf/api/manual-wf-input/":
                return {"hydra:member": [{"id": 5, "step_id": 77, "workflow": "gAAAA-token"}]}
            return {}

        def post(self, endpoint, data=None, params=None, **kw):
            self.posts.append((endpoint, data, params))
            if "retrieve_wfinput" in endpoint:
                return {
                    "id": 5,
                    "workflow": 42,
                    "step_id": 77,
                    "response_mapping": {
                        "options": [
                            {"option": "Approve", "primary": True, "step_iri": "/api/3/workflow_steps/app"},
                            {"option": "Reject", "step_iri": "/api/3/workflow_steps/rej"},
                        ]
                    },
                }
            if "wfinput_resume" in endpoint:
                return {"task_id": "t-1", "message": "Awaiting Playbook resumed successfully."}
            return {}

    c = _ApprovalClient()
    res = PlaybooksAPI(c).approval("149900", decision="approve")
    # GET the run-scoped manual-wf-input queue
    ep, params = c.gets[0]
    assert ep == "/api/wf/api/manual-wf-input/"
    assert params["workflow"] == "149900"
    # resume POSTs to wfinput_resume (NOT /approval/)
    resume = next(p for p in c.posts if "wfinput_resume" in p[0])
    assert resume[0] == "/api/wf/api/workflows/42/wfinput_resume/"
    assert resume[1]["manual_input_id"] == 5
    assert resume[1]["step_iri"] == "/api/3/workflow_steps/app"  # primary option
    assert resume[1]["step_id"] == 77
    assert resume[1]["user"] == "/api/3/people/admin"
    assert not any("/approval/" in p[0] for p in c.posts)  # old endpoint never hit
    assert res.task_id == "t-1"


def test_approval_reject_picks_non_primary_option():
    from pyfsr.api.manual_input import ManualInputAPI

    class _C:
        def __init__(self):
            self.manual_input = ManualInputAPI(self)
            self.posts = []

        def get(self, endpoint, params=None, **kw):
            if endpoint == "/api/3/people":
                return {"hydra:member": [{"@id": "/api/3/people/admin", "firstname": "Admin"}]}
            if endpoint == "/api/wf/api/manual-wf-input/":
                return {"hydra:member": [{"id": 5, "step_id": 77, "workflow": "tok"}]}
            return {}

        def post(self, endpoint, data=None, params=None, **kw):
            self.posts.append((endpoint, data))
            if "retrieve_wfinput" in endpoint:
                return {
                    "id": 5,
                    "workflow": 42,
                    "step_id": 77,
                    "response_mapping": {
                        "options": [
                            {"option": "Approve", "primary": True, "step_iri": "/app"},
                            {"option": "Reject", "step_iri": "/rej"},
                        ]
                    },
                }
            if "wfinput_resume" in endpoint:
                return {"task_id": "t-1", "message": "ok"}
            return {}

    c = _C()
    PlaybooksAPI(c).approval("149900", decision="reject")
    resume = next(p for p in c.posts if "wfinput_resume" in p[0])
    assert resume[1]["step_iri"] == "/rej"  # non-primary option


def test_approval_raises_for_legacy_approval_step():
    # A legacy `type: approval` step creates NO manual-wf-input -> the run-scoped
    # GET returns empty -> pb.approval raises ValueError with remediation guidance.
    class _EmptyClient:
        manual_input = None  # never reached (raises before use)

        def get(self, endpoint, params=None, **kw):
            if endpoint == "/api/wf/api/manual-wf-input/":
                return {"hydra:member": []}
            return {}

        def post(self, *a, **kw):
            raise AssertionError("should not resume when there is no pending input")

    with pytest.raises(ValueError, match="NOT.*programmatically resumable"):
        PlaybooksAPI(_EmptyClient()).approval("149900", decision="approve")


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
    assert resp["task_id"] == "t99"


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
    assert resp["task_id"] == "ta1"


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
            "@id": "/api/3/workflow_steps/22222222-2222-2222-2222-222222222222",
            "@type": "WorkflowStep",
            "uuid": "22222222-2222-2222-2222-222222222222",
            "name": "Start",
            "group": "/api/3/workflow_groups/33333333-3333-3333-3333-333333333333",
            "stepType": {
                "@id": "/api/3/workflow_step_types/99999999-9999-9999-9999-999999999999",
                "name": "cybersponse.abstract_trigger",
                "uuid": "99999999-9999-9999-9999-999999999999",
            },
        },
        {
            "@id": "/api/3/workflow_steps/44444444-4444-4444-4444-444444444444",
            "@type": "WorkflowStep",
            "id": 7,
            "uuid": "44444444-4444-4444-4444-444444444444",
            "name": "Action",
        },
    ],
    "routes": [
        {
            "@id": "/api/3/workflow_routes/55555555-5555-5555-5555-555555555555",
            "@type": "WorkflowRoute",
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


def test_clone_strips_nested_entity_ids_and_keeps_steptype():
    # Regression: an inlined step/route carrying its own ``@id`` makes the
    # appliance treat it as a reference to an existing (now-nonexistent) row and
    # fail the POST with EntityNotFoundException. The clone must drop nested
    # @id/@type/id on every step/route while leaving the shared stepType (and its
    # @id, which points at a real step-type row) untouched.
    c = _Rec(resp=_SRC_DEFINITION)
    PlaybooksAPI(c).clone("11111111-1111-1111-1111-111111111111", "Copy")
    body = c.calls[-1][2]
    for step in body["steps"]:
        assert "@id" not in step
        assert "@type" not in step
        assert "id" not in step
    for route in body["routes"]:
        assert "@id" not in route
        assert "@type" not in route
    # stepType (and its real @id) is preserved verbatim — it is NOT a clone-owned uuid.
    st = body["steps"][0]["stepType"]
    assert st["@id"] == "/api/3/workflow_step_types/99999999-9999-9999-9999-999999999999"
    assert st["uuid"] == "99999999-9999-9999-9999-999999999999"


def test_clone_transform_hook_mutates_body_before_post():
    c = _Rec(resp=_SRC_DEFINITION)

    def _rename(body):
        body["steps"][1]["name"] = "Renamed Action"
        return body

    PlaybooksAPI(c).clone("11111111-1111-1111-1111-111111111111", "Copy", transform=_rename)
    body = c.calls[-1][2]
    assert body["steps"][1]["name"] == "Renamed Action"


def test_clone_transform_may_edit_in_place_without_returning():
    c = _Rec(resp=_SRC_DEFINITION)

    def _edit(body):
        body["description"] = "edited"
        # returns None — clone() must fall back to the mutated body

    PlaybooksAPI(c).clone("11111111-1111-1111-1111-111111111111", "Copy", transform=_edit)
    body = c.calls[-1][2]
    assert body["description"] == "edited"


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


def test_last_run_preserves_full_record_in_extra():
    raw_run = _run("/api/wf/api/workflows/a/", "A", "failed", "t", result={"error": "boom"})
    client = FakeClient(workflows=[raw_run])
    run = PlaybooksAPI(client).last_run(playbook_uuid="pb-uuid")
    assert run is not None
    assert run.pk == "a"
    assert "@id" in run
    assert run["@id"] == "/api/wf/api/workflows/a/"
    assert run.error_message == "boom"


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


def test_why_failed_skips_incipient_downstream_steps():
    """A non-last step failing leaves downstream steps ``incipient``; why_failed must
    report the actual failed step, not the first incipient one (regression: run 686500)."""
    run_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "name": "Recon",
        "status": "failed",
        "modified": "2026-06-08T03:00",
        "uuid": "u1",
    }
    full_record = {
        "@id": "/api/wf/api/workflows/run1/",
        "status": "failed",
        "result": {},
        # Step order here is NOT execution order: an ``incipient`` (never-ran) step
        # appears BEFORE the actually-failed step, exactly as on run 686500. The old
        # predicate matched the first non-(finished/success/running) step and so
        # reported "Email report"; the fix must skip it and report "Write finding".
        "steps": [
            {"name": "Start", "status": "finished", "result": {}},
            {"name": "Email report", "status": "incipient", "result": {}},
            {"name": "Write finding", "status": "failed", "result": {"error": "real failure"}},
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
    assert result["failing_step"] == "Write finding"
    assert result["error_message"] == "real failure"


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


def test_get_execution_step_detail_exposes_full_record():
    """The full step_detail record (steps/env) rides in the typed RunSummary's
    extra — no raw flag needed; callers reach it by item access."""
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

    class _StepDetailClient(FakeClient):
        def get(self, endpoint, params=None, **kw):
            if "step_detail=true" in endpoint:
                return full_record
            return super().get(endpoint, params, **kw)

    client = _StepDetailClient(workflows=[full_record])
    result = PlaybooksAPI(client).get_execution("run1", step_detail=True)

    assert "steps" in result
    assert "env" in result
    assert len(result["steps"]) == 2
    assert result.status == "failed"


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


def test_why_failed_derives_pk_from_run_iri():
    """pk is always derived from the run's @id (the wire never sends a pk field),
    so it's consistent regardless of what the record carries."""
    run_record = {
        "@id": "/api/wf/api/workflows/extracted-pk/",
        "name": "PB",
        "status": "failed",
        "modified": "t",
        "task_id": "t1",
        "uuid": "u1",
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

    assert result is not None
    assert result.pk == "extracted-pk"


# -- typed pydantic shapes --------------------------------------------------
def test_shape_run_returns_typed_run_summary():
    from pyfsr.models import RunSummary

    s = _shape_run(_run("/api/wf/api/workflows/abc/", "n", "finished", "t", task_id="t1"))
    assert isinstance(s, RunSummary)
    # dict-compatible access still works
    assert s["pk"] == "abc" and s.pk == "abc"
    assert s.task_id == "t1"
    assert "status" in s


def test_run_env_returns_typed_run_env():
    from pyfsr.models import RunEnv, RunStep

    client = FakeClient(workflows=[_run_with_steps("901")])
    env = PlaybooksAPI(client).run_env("901")
    assert isinstance(env, RunEnv)
    assert isinstance(env.steps["Fetch Email"], RunStep)
    assert env.status == "finished"


def test_trigger_returns_typed_trigger_response():
    from pyfsr.models import TriggerResponse

    c = _Rec(resp={"task_id": "t99"})
    resp = PlaybooksAPI(c).trigger("aabbccdd-0000-0000-0000-000000000000")
    assert isinstance(resp, TriggerResponse)
    assert resp.task_id == "t99"


def test_trigger_response_absorbs_the_action_routes_plural_task_ids():
    """The action route answers `task_ids` (plural); both accessors must still work.

    Live-verified: POST /api/triggers/1/action/<route> returns
    {"task_ids": [...]} while notrigger returns {"task_id": ...}. Only `task_id`
    was declared, so the plural key fell into model_extra while the `task_ids`
    PROPERTY (which normalizes `task_id`) shadowed it and returned [] — leaving a
    trigger_action caller unable to reach the run they just started.
    """
    from pyfsr.models import TriggerResponse

    resp = TriggerResponse(**{"task_ids": ["run-1"]})
    assert resp.task_ids == ["run-1"], "the wire's plural key must be reachable"
    assert resp.task_id == ["run-1"], "folded into task_id (documented as str|list)"

    # the scalar notrigger shape is untouched
    scalar = TriggerResponse(**{"task_id": "run-2"})
    assert scalar.task_ids == ["run-2"]
    assert scalar.task_id == "run-2"

    # an explicit task_id always wins over a stray plural key
    both = TriggerResponse(**{"task_id": "real", "task_ids": ["ignored"]})
    assert both.task_id == "real"


def test_why_failed_returns_typed_run_failure():
    from pyfsr.models import RunFailure

    runs_resp = {"hydra:member": [{"@id": "/api/wf/api/workflows/rk/", "status": "failed", "modified": "t"}]}
    detail = {
        "@id": "/api/wf/api/workflows/rk/",
        "status": "failed",
        "result": {"error": "top boom"},
        "steps": [{"name": "Do Thing", "status": "failed", "result": {"message": "step boom"}}],
    }

    class _C:
        def get(self, endpoint, params=None, **kw):
            if "/rk/" in endpoint:  # get_execution(step_detail=True) fetch
                return detail
            return runs_resp  # run-history list fetches

        def post(self, endpoint, data=None, params=None, **kw):
            return runs_resp

    result = PlaybooksAPI(_C()).why_failed(playbook_uuid="pb-uuid")
    assert isinstance(result, RunFailure)
    assert result.failing_step == "Do Thing"
    assert result["error_message"] == "step boom"


def test_trigger_request_normalizes_records():
    from pyfsr.models import TriggerRequest

    req = TriggerRequest(records="abc-123")
    assert req.to_body()["records"] == ["/api/3/alerts/abc-123"]
    req2 = TriggerRequest(records=["/api/3/incidents/x", "y"])
    assert req2.to_body()["records"] == ["/api/3/incidents/x", "/api/3/alerts/y"]


def test_create_playbook_request_builds_body_and_validates():
    from pyfsr.models import CreatePlaybookRequest

    body = CreatePlaybookRequest(name="PB", collection="coll-uuid", priority="/api/3/picklists/p").to_body()
    assert body["collection"] == "/api/3/workflow_collections/coll-uuid"
    assert body["isActive"] is True
    assert body["priority"] == "/api/3/picklists/p"
    with pytest.raises(Exception):
        CreatePlaybookRequest(name="  ", collection="c")


def test_create_playbook_blank_name_raises_value_error():
    with pytest.raises(ValueError, match="non-empty name"):
        PlaybooksAPI(_Rec()).create_playbook("   ", "coll-uuid")


# -- child_runs / has_async_children (async loop sub-playbook lookup) --------
def test_child_runs_scopes_by_parent_wf_pk():
    kids = [
        _run(
            "/api/wf/api/workflows/301/",
            "Child",
            "finished",
            "2026-06-26T00:00:02",
            created="2026-06-26T00:00:00",
            parent_wf="/wf/api/workflows/210/",
        ),
        _run(
            "/api/wf/api/workflows/302/",
            "Child",
            "finished",
            "2026-06-26T00:00:03",
            created="2026-06-26T00:00:01",
            parent_wf="/wf/api/workflows/210/",
        ),
    ]
    client = FakeClient(workflows=kids)
    runs = PlaybooksAPI(client).child_runs(210)
    assert len(runs) == 2
    # the live-table query must scope by parent_wf=<pk>, NOT the parent_wf__isnull
    # default that execution_history uses (which would exclude children).
    qs = [ep for ep, _ in client.get_calls if ep.startswith("/api/wf/api/workflows/?")]
    assert any("parent_wf=210" in ep for ep in qs)
    assert all("parent_wf__isnull=True" not in ep for ep in qs)


def test_child_runs_resolves_path_and_taskid():
    api = PlaybooksAPI(FakeClient())
    assert api._resolve_run_pk("/wf/api/workflows/210/") == "210"
    assert api._resolve_run_pk(210) == "210"
    assert api._resolve_run_pk("not-a-pk") is None


def test_has_async_children_reads_tag():
    parent = _run(
        "/api/wf/api/workflows/210/", "Parent", "finished", "2026-06-26T00:00:05", tags="#has_async_childwf_cyops"
    )
    client = FakeClient(workflows=[parent])
    assert PlaybooksAPI(client).has_async_children(210) is True


# -- trigger request_timeout + status() (operational helpers) -----------------
class _KwRec:
    """Records post() keyword args so timeout forwarding can be asserted."""

    def __init__(self, resp=None):
        self.post_kwargs = []
        self._resp = resp if resp is not None else {"task_id": "t1"}

    def get(self, endpoint, params=None, **kw):
        return {"hydra:member": [{"uuid": "pb-uuid"}]}

    def post(self, endpoint, data=None, params=None, **kw):
        self.post_kwargs.append(kw)
        return self._resp


def test_trigger_forwards_request_timeout():
    c = _KwRec()
    PlaybooksAPI(c).trigger("aabbccdd-0000-0000-0000-000000000000", request_timeout=8)
    assert c.post_kwargs[0].get("timeout") == 8


def test_trigger_omits_timeout_by_default():
    c = _KwRec()
    PlaybooksAPI(c).trigger("aabbccdd-0000-0000-0000-000000000000")
    assert "timeout" not in c.post_kwargs[0]


def test_trigger_by_name_forwards_request_timeout():
    c = _KwRec()
    PlaybooksAPI(c).trigger_by_name("my_webhook", request_timeout=5)
    assert c.post_kwargs[0].get("timeout") == 5


def test_trigger_action_forwards_request_timeout():
    c = _KwRec()
    PlaybooksAPI(c).trigger_action("route-1", module="alerts", record_uuid="r1", request_timeout=3)
    assert c.post_kwargs[0].get("timeout") == 3


def test_status_returns_lowercased_status():
    c = _PollClient(["Running"])
    assert PlaybooksAPI(c).status("run-uuid") == "running"


def test_status_none_when_no_run_yet():
    class _Empty:
        def post(self, endpoint, data=None, params=None, **kw):
            return {"hydra:member": []}

    assert PlaybooksAPI(_Empty()).status("run-uuid") is None


def test_status_rejects_blank_task_id():
    with pytest.raises(ValueError):
        PlaybooksAPI(_Rec()).status("  ")


def test_terminal_statuses_exposed():
    assert "finished" in PlaybooksAPI.TERMINAL_STATUSES
    assert "running" not in PlaybooksAPI.TERMINAL_STATUSES
