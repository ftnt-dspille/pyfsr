"""Unit tests for ManualInputAPI.answer() -- the one-call find+fill+resume."""

import pytest

from pyfsr.api.manual_input import ManualInputAPI


class FakeClient:
    """Stand-in for FortiSOAR that scripts the list/retrieve/resume sequence."""

    def __init__(self, *, pending=None, retrieve=None, people=None):
        self._pending = pending or []
        self._retrieve = retrieve or {}
        self._people = (
            people
            if people is not None
            else [
                {"@id": "/api/3/people/admin", "firstname": "CS", "lastname": "Admin"},
            ]
        )
        self.posts = []

    def get(self, endpoint, params=None, **kwargs):
        if endpoint == "/api/3/people":
            return {"hydra:member": self._people}
        return {}

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.posts.append((endpoint, data, params))
        if endpoint.endswith("list_wfinput/"):
            return {"hydra:member": self._pending}
        if "retrieve_wfinput" in endpoint:
            return self._retrieve
        if "wfinput_resume" in endpoint:
            return {"task_id": "t-1", "message": "Awaiting Playbook resumed successfully."}
        return {}


def _retrieve_doc(*, workflow=42, step_id=7, var="my_number", options=None):
    return {
        "id": 5,
        "workflow": workflow,
        "step_id": step_id,
        "input": {"schema": {"inputVariables": [{"name": var}]}},
        "response_mapping": {"options": options or [{"option": "Submit", "step_iri": "/api/3/wf-steps/abc"}]},
    }


def test_answer_by_title_resolves_and_resumes():
    # `title` is the prompt's SCHEMA title -- the step's `title:` -- not the
    # step name ("AskNumber"). Live-verified on 8.0.0.
    client = FakeClient(
        pending=[{"id": 5, "title": "Enter a six digit number"}],
        retrieve=_retrieve_doc(),
    )
    res = ManualInputAPI(client).answer(654321, by_title="Enter a six digit number")
    assert res.task_id == "t-1"
    resume = next(p for p in client.posts if "wfinput_resume" in p[0])
    endpoint, body, _ = resume
    assert endpoint == "/api/wf/api/workflows/42/wfinput_resume/"  # numeric run id
    assert body["step_iri"] == "/api/3/wf-steps/abc"
    assert body["step_id"] == 7
    assert body["manual_input_id"] == 5
    assert body["input"] == {"my_number": 654321}  # scalar mapped to the lone var
    assert body["user"] == "/api/3/people/admin"  # admin auto-resolved


def test_answer_by_input_id_skips_list():
    client = FakeClient(retrieve=_retrieve_doc())
    ManualInputAPI(client).answer(11, input_id=5)
    assert not any(p[0].endswith("list_wfinput/") for p in client.posts)


def test_answer_requires_exactly_one_selector():
    client = FakeClient()
    with pytest.raises(ValueError):
        ManualInputAPI(client).answer(1)
    with pytest.raises(ValueError):
        ManualInputAPI(client).answer(1, by_title="X", input_id=5)


def test_answer_unknown_title_raises_lookup():
    client = FakeClient(pending=[{"id": 5, "title": "Other"}])
    with pytest.raises(LookupError):
        ManualInputAPI(client).answer(1, by_title="Enter a six digit number")


def test_answer_by_step_name_does_not_match_and_error_explains_why():
    """Regression: `.title` is the schema title, so the STEP NAME must not match.

    The prompt below is the live shape: a step *named* "AskNumber" whose
    `title:` is "Enter a six digit number". Passing the step name once appeared
    to work only because an old compiler dropped `title:` and defaulted the
    schema title to the step name -- the two are independent on the wire.
    """
    client = FakeClient(pending=[{"id": 5, "title": "Enter a six digit number"}])
    with pytest.raises(LookupError) as exc:
        ManualInputAPI(client).answer(654321, by_title="AskNumber")
    msg = str(exc.value)
    assert "schema title" in msg  # names the real semantics
    assert "Enter a six digit number" in msg  # lists what IS pending, to fix it


def test_answer_ambiguous_title_refuses_rather_than_guessing():
    # The same step paused in two runs -> two identically-titled rows. Picking
    # one would resume an arbitrary run, so answer() must refuse.
    client = FakeClient(
        pending=[
            {"id": 5, "title": "Enter a six digit number"},
            {"id": 6, "title": "Enter a six digit number"},
        ],
        retrieve=_retrieve_doc(),
    )
    with pytest.raises(LookupError) as exc:
        ManualInputAPI(client).answer(654321, by_title="Enter a six digit number")
    assert "[5, 6]" in str(exc.value)
    # nothing was resumed
    assert not any("wfinput_resume" in p[0] for p in client.posts)


def test_answer_refuses_option_without_step_iri_instead_of_posting_null():
    """A button with no step_iri is unresumable -- fail with the cause, not a 500.

    `step_iri` is wired at author time from the step's `next:`; a Manual Input
    step with no next step compiles to an option without one. Live-verified:
    wfinput_resume returns HTTP 500 for a null *or* absent step_iri, so posting
    it anyway only yields an opaque server error.
    """
    client = FakeClient(retrieve=_retrieve_doc(options=[{"option": "Continue", "primary": True}]))
    with pytest.raises(ValueError) as exc:
        ManualInputAPI(client).answer(654321, input_id=5)
    msg = str(exc.value)
    assert "step_iri" in msg and "next" in msg
    assert not any("wfinput_resume" in p[0] for p in client.posts)


def test_answer_explicit_user_is_used_and_people_not_queried():
    queried = []

    class C(FakeClient):
        def get(self, endpoint, params=None, **kwargs):
            queried.append(endpoint)
            return super().get(endpoint, params, **kwargs)

    client = C(retrieve=_retrieve_doc())
    ManualInputAPI(client).answer(1, input_id=5, user="/api/3/people/bob")
    body = next(p[1] for p in client.posts if "wfinput_resume" in p[0])
    assert body["user"] == "/api/3/people/bob"
    assert "/api/3/people" not in queried


def test_answer_option_by_label():
    # The button label lives under the `option` key on the wire (live-verified),
    # NOT `label` -- selecting by label must match that key.
    opts = [
        {"option": "Reject", "step_iri": "/iri/reject"},
        {"option": "Approve", "step_iri": "/iri/approve"},
    ]
    client = FakeClient(retrieve=_retrieve_doc(options=opts))
    ManualInputAPI(client).answer(input_id=5, option="Approve")
    body = next(p[1] for p in client.posts if "wfinput_resume" in p[0])
    assert body["step_iri"] == "/iri/approve"
    assert body["input"] == {}  # no value -> approval/button only


def test_answer_scalar_rejected_for_multivariable_prompt():
    doc = _retrieve_doc()
    doc["input"]["schema"]["inputVariables"] = [{"name": "a"}, {"name": "b"}]
    client = FakeClient(retrieve=doc)
    with pytest.raises(ValueError):
        ManualInputAPI(client).answer(5, input_id=5)


def test_answer_inputs_dict_passes_through():
    doc = _retrieve_doc()
    doc["input"]["schema"]["inputVariables"] = [{"name": "a"}, {"name": "b"}]
    client = FakeClient(retrieve=doc)
    ManualInputAPI(client).answer(input_id=5, inputs={"a": 1, "b": 2})
    body = next(p[1] for p in client.posts if "wfinput_resume" in p[0])
    assert body["input"] == {"a": 1, "b": 2}


def test_answer_bad_option_index_raises():
    client = FakeClient(retrieve=_retrieve_doc())
    with pytest.raises(IndexError):
        ManualInputAPI(client).answer(1, input_id=5, option=9)


# ---------------------------------------------------------------------------
# pending_for_run(task_id) -- the run-scoped pending-input lookup
# ---------------------------------------------------------------------------


class _FakePlaybooks:
    """Stand-in for client.playbooks.log_list(task_id=...) -> run @id."""

    def __init__(self, run_pk=42):
        self._run_pk = run_pk
        self.log_calls = []

    def log_list(self, *, task_id=None, limit=None, **kw):
        self.log_calls.append({"task_id": task_id, "limit": limit})
        return {"hydra:member": [{"@id": f"/wf/api/workflows/{self._run_pk}/"}]}


class _RunScopedFakeClient(FakeClient):
    """FakeClient whose .get scripts the manual-wf-input list_wfinput GET."""

    def __init__(self, *, pending, run_pk=42):
        super().__init__()
        self.playbooks = _FakePlaybooks(run_pk=run_pk)
        self._pending = pending
        self.gets = []

    def get(self, endpoint, params=None, **kwargs):
        self.gets.append((endpoint, params))
        if endpoint == "/api/wf/api/manual-wf-input/":
            return {"hydra:member": self._pending, "hydra:nextPage": None}
        return super().get(endpoint, params=params, **kwargs)


def _pending_doc(*, mid=51, is_approval=False, title="Approve ingestion?"):
    return {
        "id": mid,
        "is_approval": is_approval,
        "step_id": 2159467,
        "workflow": "gAAAAABqencryptedtoken",
        "input": {"schema": {"title": title, "inputVariables": [{"name": "analyst_comment"}]}},
        "response_mapping": {"options": [{"option": "approve", "primary": True}]},
    }


def test_pending_for_run_resolves_task_id_to_run_pk_and_filters():
    client = _RunScopedFakeClient(pending=[_pending_doc()], run_pk=149763)
    out = ManualInputAPI(client).pending_for_run("105189fa-task-id")
    # task_id -> log_list -> run pk 149763
    assert client.playbooks.log_calls == [{"task_id": "105189fa-task-id", "limit": 1}]
    # GET manual-wf-input with the workflow=<run_pk> filter
    ep, params = client.gets[-1]
    assert ep == "/api/wf/api/manual-wf-input/"
    assert params["workflow"] == "149763"
    assert params["format"] == "json"
    # typed return
    assert len(out) == 1
    from pyfsr.models._system import ManualInput

    assert isinstance(out[0], ManualInput)
    assert out[0].id == 51
    assert out[0].step_id == 2159467
    assert out[0].is_approval is False
    # the full prompt survives (input.schema + response_mapping.options)
    assert out[0].input.schema_.title == "Approve ingestion?"
    assert out[0].input.schema_.inputVariables[0].name == "analyst_comment"
    assert out[0].response_mapping.options[0].option == "approve"


def test_pending_for_run_is_approval_filter_is_client_side():
    docs = [_pending_doc(mid=51, is_approval=False), _pending_doc(mid=52, is_approval=True)]
    client = _RunScopedFakeClient(pending=docs)
    out = ManualInputAPI(client).pending_for_run("tid", is_approval=True)
    assert [m.id for m in out] == [52]
    out_false = ManualInputAPI(_RunScopedFakeClient(pending=docs)).pending_for_run("tid", is_approval=False)
    assert [m.id for m in out_false] == [51]


def test_pending_for_run_empty_when_no_run_matches_task_id():
    class _NoRunPlaybooks:
        def log_list(self, *, task_id=None, limit=None, **kw):
            return {"hydra:member": []}

    class _C(FakeClient):
        def __init__(self):
            super().__init__()
            self.playbooks = _NoRunPlaybooks()
            self.gets = []

        def get(self, endpoint, params=None, **kwargs):
            self.gets.append(endpoint)
            return {}

    client = _C()
    assert ManualInputAPI(client).pending_for_run("nope") == []
    assert client.gets == []  # never queried manual-wf-input


# ---------------------------------------------------------------------------
# Captured wire shapes -- these pin the fixtures to what a live 8.0.0 box
# actually returns. The three endpoints do NOT share a shape, and an earlier
# fixture invented one that merged them (a list row carrying `input` and a
# `step_iri`), which let a wrong `.title` claim go unnoticed.
# ---------------------------------------------------------------------------


def test_captured_list_row_is_summary_only_and_title_is_the_schema_title():
    from pyfsr._testing import client_captures as cap
    from pyfsr.models._system import ManualInput

    row = ManualInput.model_validate(cap.MANUAL_INPUT_LIST_RESPONSE["hydra:member"][0])
    # POST list_wfinput/ omits the prompt entirely -- these only exist on the
    # full shapes. A fixture that carries them here is fiction.
    assert row.input is None
    assert row.response_mapping is None
    assert row.custom_fields is None
    # ...and `workflow` is the encrypted Fernet token, not the numeric run id.
    assert isinstance(row.workflow, str) and row.workflow.startswith("gAAAAA")
    # The captured prompt is step "AskNumber" with title: "Enter a six digit
    # number" -- title is the latter, so the two can never be conflated again.
    assert row.title == "Enter a six digit number"


def test_captured_retrieve_is_full_and_carries_the_numeric_run_id():
    from pyfsr._testing import client_captures as cap
    from pyfsr.models._system import ManualInput

    full = ManualInput.model_validate(cap.MANUAL_INPUT_RETRIEVE_RESPONSE)
    assert full.workflow == 1  # numeric run id -- what wfinput_resume needs
    assert full.input.schema_.title == "Enter a six digit number"
    assert full.input.schema_.inputVariables[0].name == "my_number"
    opt = full.response_mapping.options[0]
    # step_iri is an /api/3/workflow_steps/<uuid> IRI (not a /api/wf/... path).
    assert opt.step_iri.startswith("/api/3/workflow_steps/")


def test_captured_pending_for_run_row_is_full_unlike_the_list_row():
    from pyfsr._testing import client_captures as cap
    from pyfsr.models._system import ManualInput

    # GET manual-wf-input/?workflow=<pk> returns the FULL row -- verified live.
    row = ManualInput.model_validate(cap.APPROVAL_MANUAL_INPUT_LIST_RESPONSE["hydra:member"][0])
    assert row.input is not None and row.response_mapping is not None
    assert row.workflow == 2  # numeric, so no follow-up retrieve() is needed
    # An approval gate is a manual_input with is_approval -- the wire reports it
    # as ApprovalManualInput. (The legacy `approval` step type never lands here.)
    assert row.is_approval is True
    assert row.type == "ApprovalManualInput"
    assert row.input.schema_.inputVariables == []  # button-only prompt
    # The non-primary button omits `primary` rather than sending False.
    assert row.response_mapping.options[0].primary is True
    assert row.response_mapping.options[1].primary is None


def test_captured_unwired_prompt_has_no_step_iri():
    from pyfsr._testing import client_captures as cap
    from pyfsr.models._system import ManualInput

    row = ManualInput.model_validate(cap.UNWIRED_MANUAL_INPUT_RETRIEVE_RESPONSE)
    # A Manual Input step with no `next:` -> its button carries no step_iri.
    assert row.response_mapping.options[0].step_iri is None
