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
        "response_mapping": {"options": options or [{"label": "Submit", "step_iri": "/api/3/wf-steps/abc"}]},
    }


def test_answer_by_title_resolves_and_resumes():
    client = FakeClient(
        pending=[{"id": 5, "title": "AskNumber"}],
        retrieve=_retrieve_doc(),
    )
    res = ManualInputAPI(client).answer(654321, by_title="AskNumber")
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
        ManualInputAPI(client).answer(1, by_title="AskNumber")


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
    opts = [
        {"label": "Reject", "step_iri": "/iri/reject"},
        {"label": "Approve", "step_iri": "/iri/approve"},
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
