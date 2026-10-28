"""Unit tests for the manual_input enhancements: find()/pending_for_record,
approve()/reject(), input coercion, and the list() default scope.

Live-verified counterparts ran against an 8.0.0 box; these lock the wire
contract offline.
"""

import datetime as dt
from pathlib import Path

import pytest

from pyfsr.api.manual_input import ManualInputAPI, _record_iri

_BASE = "/api/wf/api/manual-wf-input/"


class FakeAttachments:
    def __init__(self):
        self.uploaded = []

    def create_from_file(self, path, **kw):
        self.uploaded.append(path)
        return {"@id": "/api/3/attachments/up1", "name": path}


class FakeClient:
    """Scripts GET list, POST list_wfinput/retrieve/resume."""

    def __init__(self, *, get_rows=None, retrieve=None, list_rows=None, people=None):
        self._get_rows = get_rows or []
        self._retrieve = retrieve or {}
        self._list_rows = list_rows if list_rows is not None else []
        self._people = people if people is not None else [{"@id": "/api/3/people/admin"}]
        self.timeout = 30
        self.attachments = FakeAttachments()
        self.gets = []
        self.posts = []

    def get(self, endpoint, params=None, **kw):
        self.gets.append((endpoint, params or {}))
        if endpoint == "/api/3/people":
            return {"hydra:member": self._people}
        if endpoint == _BASE:
            return {"hydra:member": self._get_rows, "hydra:totalItems": len(self._get_rows)}
        return {}

    def post(self, endpoint, data=None, params=None, **kw):
        self.posts.append((endpoint, data, params))
        if endpoint.endswith("list_wfinput/"):
            return {"hydra:member": self._list_rows}
        if "retrieve_wfinput" in endpoint:
            return self._retrieve
        if "wfinput_resume" in endpoint:
            return {"task_id": "t-1", "message": "Awaiting Playbook resumed successfully."}
        return {}


def _full(*, options, variables=None, workflow=42, step_id=7):
    return {
        "id": 9,
        "workflow": workflow,
        "step_id": step_id,
        "input": {"schema": {"inputVariables": variables or []}},
        "response_mapping": {"options": options},
    }


# ---- find() -------------------------------------------------------------


def test_find_builds_get_with_filter_params_and_record_iri():
    c = FakeClient(get_rows=[{"id": 1, "type": "DecisionBased", "is_approval": True}])
    ManualInputAPI(c).find(record={"@id": "/api/3/alerts/abc"}, type="DecisionBased", workflow=184988, limit=10)
    endpoint, params = c.gets[-1]
    assert endpoint == _BASE
    assert params["record"] == "/api/3/alerts/abc"
    assert params["type"] == "DecisionBased"
    assert params["workflow"] == 184988


def test_find_rejects_bad_type_and_bad_ordering():
    c = FakeClient()
    with pytest.raises(ValueError):
        ManualInputAPI(c).find(type="ApprovalManualInput")  # not a real wire type
    with pytest.raises(ValueError):
        ManualInputAPI(c).find(ordering="title")  # not in ordering_fields
    with pytest.raises(ValueError):
        ManualInputAPI(c).find(ordering="name")  # declared but 500s (phantom column)


def test_find_is_approval_is_client_side():
    rows = [
        {"id": 1, "is_approval": True, "type": "DecisionBased"},
        {"id": 2, "is_approval": False, "type": "InputBased"},
    ]
    c = FakeClient(get_rows=rows)
    out = ManualInputAPI(c).find(is_approval=True, limit=50)
    assert [m.id for m in out] == [1]
    # the GET itself carried no is_approval param (server ignores it)
    assert "is_approval" not in c.gets[-1][1]


def test_pending_for_record_delegates_to_find():
    c = FakeClient(get_rows=[])
    ManualInputAPI(c).pending_for_record("/api/3/incidents/xyz", limit=5)
    assert c.gets[-1][1]["record"] == "/api/3/incidents/xyz"


# ---- list() default -----------------------------------------------------


def test_list_default_scope_is_all():
    c = FakeClient(list_rows=[])
    ManualInputAPI(c).list(limit=1)
    body = c.posts[-1][1]
    assert body["assigned_to"] == "all"


# ---- approve()/reject() -------------------------------------------------


def test_approve_presses_primary_and_reject_presses_other():
    opts = [
        {"option": "Approve", "primary": True, "step_iri": "/api/3/wf-steps/app"},
        {"option": "Reject", "step_iri": "/api/3/wf-steps/rej"},
    ]
    c = FakeClient(retrieve=_full(options=opts))
    ManualInputAPI(c).approve(input_id=9)
    assert c.posts[-1][1]["step_iri"] == "/api/3/wf-steps/app"

    c2 = FakeClient(retrieve=_full(options=opts))
    ManualInputAPI(c2).reject(input_id=9)
    assert c2.posts[-1][1]["step_iri"] == "/api/3/wf-steps/rej"


def test_reject_on_single_option_raises():
    c = FakeClient(retrieve=_full(options=[{"option": "OK", "primary": True, "step_iri": "/x"}]))
    with pytest.raises(LookupError):
        ManualInputAPI(c).reject(input_id=9)


def test_approve_and_reject_never_resolve_to_the_same_option():
    # SECURITY: two buttons, neither flagged primary and neither label recognizable
    # -> we must NOT silently press the same button for both. Both must raise.
    opts = [
        {"option": "Button One", "step_iri": "/api/3/wf-steps/a"},
        {"option": "Button Two", "step_iri": "/api/3/wf-steps/b"},
    ]
    with pytest.raises(LookupError):
        ManualInputAPI(FakeClient(retrieve=_full(options=opts))).approve(input_id=9)
    with pytest.raises(LookupError):
        ManualInputAPI(FakeClient(retrieve=_full(options=opts))).reject(input_id=9)


def test_reject_uses_label_not_primary_when_approve_is_primary():
    # Approve is primary; Reject identified by label -> distinct step_iris.
    opts = [
        {"option": "Approve", "primary": True, "step_iri": "/api/3/wf-steps/app"},
        {"option": "Deny", "step_iri": "/api/3/wf-steps/deny"},
    ]
    c = FakeClient(retrieve=_full(options=opts))
    ManualInputAPI(c).reject(input_id=9)
    assert c.posts[-1][1]["step_iri"] == "/api/3/wf-steps/deny"


_GATE = [
    {"option": "Approve", "primary": True, "step_iri": "/x-app"},
    {"option": "Reject", "step_iri": "/x-rej"},
]


def test_comment_maps_into_a_comment_field():
    c = FakeClient(retrieve=_full(options=_GATE, variables=[{"name": "approver_comment"}]))
    ManualInputAPI(c).approve(input_id=9, comment="ship it")
    assert c.posts[-1][1]["input"] == {"approver_comment": "ship it"}


def test_comment_without_a_field_raises():
    c = FakeClient(retrieve=_full(options=_GATE, variables=[]))
    with pytest.raises(ValueError):
        ManualInputAPI(c).approve(input_id=9, comment="nowhere to go")


# ---- coercion -----------------------------------------------------------


def test_coerce_datetime_and_record_and_file(tmp_path):
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("proof")
    opts = [{"option": "Submit", "primary": True, "step_iri": "/x"}]
    variables = [
        {"name": "when"},
        {"name": "host"},
        {"name": "evidence", "formType": "file"},
        {"name": "note"},
    ]
    c = FakeClient(retrieve=_full(options=opts, variables=variables))
    ManualInputAPI(c).answer(
        input_id=9,
        inputs={
            "when": dt.datetime(2026, 1, 2, 3, 4, 5),
            "host": {"@id": "/api/3/assets/h1"},
            "evidence": Path(evidence),  # explicit Path -> uploaded
            "note": "plain text stays",
        },
    )
    body = c.posts[-1][1]["input"]
    assert body["when"] == "2026-01-02T03:04:05"
    assert body["host"] == "/api/3/assets/h1"
    assert body["evidence"] == "/api/3/attachments/up1"
    assert body["note"] == "plain text stays"
    assert c.attachments.uploaded == [str(evidence)]


def test_string_is_never_auto_uploaded_even_on_a_file_field(tmp_path):
    # SECURITY: a plain str that happens to be a real local path on a server-
    # declared file field must NOT be read/uploaded -- only an explicit Path is.
    real = tmp_path / "secret.txt"
    real.write_text("do not exfiltrate")
    opts = [{"option": "Submit", "primary": True, "step_iri": "/x"}]
    c = FakeClient(retrieve=_full(options=opts, variables=[{"name": "evidence", "formType": "file"}]))
    ManualInputAPI(c).answer(input_id=9, inputs={"evidence": str(real)})
    assert c.posts[-1][1]["input"]["evidence"] == str(real)  # sent as-is
    assert c.attachments.uploaded == []  # nothing uploaded


def test_path_that_is_not_a_file_raises(tmp_path):
    opts = [{"option": "Submit", "primary": True, "step_iri": "/x"}]
    c = FakeClient(retrieve=_full(options=opts, variables=[{"name": "evidence", "formType": "file"}]))
    with pytest.raises(ValueError):
        ManualInputAPI(c).answer(input_id=9, inputs={"evidence": Path(tmp_path / "missing.bin")})


def test_record_iri_helper():
    assert _record_iri("/api/3/alerts/x") == "/api/3/alerts/x"
    assert _record_iri({"@id": "/api/3/alerts/y"}) == "/api/3/alerts/y"
    with pytest.raises(ValueError):
        _record_iri({"no": "iri"})
