"""Unit tests for the playbook version-control surface (``client.playbooks``).

Pins the wire each method emits and the typed shapes it returns, using a fake
client that records calls. Live wire verified on 8.0.0 (.159): versions are
saved snapshots in the ``workflow_versions`` module (not a revision resource).
"""

import json

import pytest

from pyfsr.api.playbooks import PlaybooksAPI, _diff_snapshots, _prepare_version_body
from pyfsr.models import PlaybookVersion, VersionDiff, Workflow

_V1_UUID = "11111111-0000-0000-0000-000000000001"
_V2_UUID = "22222222-0000-0000-0000-000000000002"
_WF_UUID = "aaaaaaaa-0000-0000-0000-0000000000aa"
_WF_IRI = f"/api/3/workflows/{_WF_UUID}"


def _steps():
    return [
        {"uuid": "s1", "name": "Step One", "stepType": {"name": "SetVariable"}, "arguments": {"x": 1}},
        {"uuid": "s2", "name": "Step Two", "stepType": {"name": "Connectors"}, "arguments": {"op": "block"}},
    ]


def _steps_v2():
    # v2: step s2's arguments changed (and name) — backs the diff tests
    return [
        {"uuid": "s1", "name": "Step One", "stepType": {"name": "SetVariable"}, "arguments": {"x": 1}},
        {
            "uuid": "s2",
            "name": "Step Two!",
            "stepType": {"name": "Connectors"},
            "arguments": {"op": "block", "comment": "c"},
        },
    ]


def _version(uuid, note, *, autosave=False, steps=None, modify=1780000000.0):
    steps = steps if steps is not None else _steps()
    return {
        "@id": f"/api/3/workflow_versions/{uuid}",
        "@type": "WorkflowVersion",
        "id": 1,
        "uuid": uuid,
        "note": note,
        "autosave": autosave,
        "json": json.dumps({"@type": "Workflow", "name": "PB", "steps": steps, "routes": [], "groups": []}),
        "workflow": {"@id": _WF_IRI, "@type": "Workflow", "name": "PB", "uuid": _WF_UUID},
        "createDate": modify,
        "modifyDate": modify,
    }


class _VersionsFakeClient:
    """Records calls; returns canned version + workflow responses."""

    def __init__(self):
        self.calls = []  # (method, endpoint, params/data)
        self._created = None

    def get(self, endpoint, params=None, **kwargs):
        self.calls.append(("GET", endpoint, params))
        if endpoint.startswith("/api/3/workflows?"):  # name lookup
            return {"hydra:member": [{"uuid": _WF_UUID, "name": "PB"}]}
        if endpoint == "/api/3/workflows":  # list
            return {"hydra:member": [{"uuid": _WF_UUID, "name": "PB"}]}
        if endpoint == f"/api/3/workflows/{_WF_UUID}":  # get_definition
            return {"@id": _WF_IRI, "name": "PB", "uuid": _WF_UUID, "steps": _steps(), "routes": [], "groups": []}
        if endpoint == "/api/3/workflow_versions":  # list_versions
            return {"hydra:member": [_version(_V1_UUID, "v1"), _version(_V2_UUID, "v2", steps=_steps_v2())]}
        if endpoint == f"/api/3/workflow_versions/{_V1_UUID}":
            return _version(_V1_UUID, "v1")
        if endpoint == f"/api/3/workflow_versions/{_V2_UUID}":
            return _version(_V2_UUID, "v2", steps=_steps_v2())
        raise AssertionError(f"unexpected GET {endpoint}")

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.calls.append(("POST", endpoint, data))
        if endpoint == "/api/3/workflow_versions":
            self._created = data
            return {**_version("33333333-0000-0000-0000-000000000003", "v3"), "json": None}
        raise AssertionError(f"unexpected POST {endpoint}")

    def put(self, endpoint, data=None, params=None, **kwargs):
        self.calls.append(("PUT", endpoint, data))
        if endpoint == f"/api/3/workflows/{_WF_UUID}":
            return {"@id": _WF_IRI, "name": "PB", "uuid": _WF_UUID, "steps": _steps(), "routes": [], "groups": []}
        raise AssertionError(f"unexpected PUT {endpoint}")

    def delete(self, endpoint, params=None, **kwargs):
        self.calls.append(("DELETE", endpoint, params))


# -- list_versions ----------------------------------------------------------
def test_list_versions_resolves_name_and_returns_typed():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    vers = pb.list_versions("PB")
    assert all(isinstance(v, PlaybookVersion) for v in vers)
    assert [v.uuid for v in vers] == [_V1_UUID, _V2_UUID]
    # name lookup hit the workflows endpoint
    assert any(ep.startswith("/api/3/workflows?") for _, ep, _ in c.calls)
    # the versions list call carried workflow=<uuid> + $includeData
    _, _, params = next(call for call in c.calls if call[1] == "/api/3/workflow_versions")
    assert params["workflow"] == _WF_UUID
    assert params["$includeData"] == "true"


def test_list_versions_accepts_uuid_directly():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    pb.list_versions(_WF_UUID)
    # no name lookup needed when a uuid is passed
    assert not any(ep.startswith("/api/3/workflows?") for _, ep, _ in c.calls)


def test_list_versions_include_data_false_omits_param():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    pb.list_versions("PB", include_data=False)
    _, _, params = next(call for call in c.calls if call[1] == "/api/3/workflow_versions")
    assert "$includeData" not in params


def test_list_versions_unknown_name_raises():
    c = _VersionsFakeClient()
    # force name lookup to return nothing
    c.get = lambda endpoint, params=None, **kw: {"hydra:member": []} if endpoint.startswith("/api/3/workflows?") else {}
    pb = PlaybooksAPI(c)
    with pytest.raises(ValueError, match="list_versions"):
        pb.list_versions("no-such-playbook")


# -- get_version ------------------------------------------------------------
def test_get_version_by_uuid():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    v = pb.get_version(_V1_UUID)
    assert isinstance(v, PlaybookVersion)
    assert v.note == "v1"
    assert v.autosave is False
    assert v.workflow_iri == _WF_IRI
    assert len(v.parsed_json()["steps"]) == 2


def test_get_version_by_iri_strips_prefix():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    pb.get_version(f"/api/3/workflow_versions/{_V2_UUID}")
    assert ("GET", f"/api/3/workflow_versions/{_V2_UUID}", {"$includeData": "true"}) in c.calls


def test_get_version_rejects_non_uuid():
    pb = PlaybooksAPI(_VersionsFakeClient())
    with pytest.raises(ValueError, match="get_version"):
        pb.get_version("not-a-uuid")


def test_parsed_json_raises_when_absent():
    v = PlaybookVersion(uuid=_V1_UUID, note="x")
    with pytest.raises(ValueError, match="no json payload"):
        v.parsed_json()


# -- create_version ---------------------------------------------------------
def test_create_version_posts_snapshot_bundle():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    created = pb.create_version("PB", note="v3")
    assert isinstance(created, PlaybookVersion)
    assert created.snapshot is None  # server does not echo the blob on POST
    method, ep, body = next(call for call in c.calls if call[0] == "POST" and call[1] == "/api/3/workflow_versions")
    assert set(body.keys()) == {"note", "json", "workflow", "modifyDate"}
    assert body["note"] == "v3"
    assert body["workflow"] == _WF_IRI
    assert isinstance(body["modifyDate"], int)
    # the json payload is a stringified workflow with server-managed fields stripped
    parsed = json.loads(body["json"])
    assert "versions" not in parsed and "collection" not in parsed
    assert parsed["name"] == "PB"


def test_prepare_version_body_strips_and_normalizes():
    wf = {
        "name": "x",
        "versions": [1],
        "collection": "c",
        "modifyUser": "u",
        "modifyDate": 9,
        "steps": {"a": {"uuid": "a"}},
        "groups": {"g": {"uuid": "g"}},
    }
    pre = _prepare_version_body(wf)
    for k in ("versions", "collection", "modifyUser", "modifyDate"):
        assert k not in pre
    assert pre["steps"] == [{"uuid": "a"}]
    assert pre["groups"] == [{"uuid": "g"}]


# -- restore_version --------------------------------------------------------
def test_restore_version_gets_then_puts():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    wf = pb.restore_version("PB", _V1_UUID)
    assert isinstance(wf, Workflow)
    assert wf.name == "PB"
    # fetched the version, then PUT the parsed json back onto the workflow
    assert ("GET", f"/api/3/workflow_versions/{_V1_UUID}", {"$includeData": "true"}) in c.calls
    put = next(call for call in c.calls if call[0] == "PUT")
    assert put[1] == f"/api/3/workflows/{_WF_UUID}"


# -- delete_version ---------------------------------------------------------
def test_delete_version_emits_delete():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    pb.delete_version(_V1_UUID)
    assert ("DELETE", f"/api/3/workflow_versions/{_V1_UUID}", None) in c.calls


def test_delete_version_rejects_non_uuid():
    pb = PlaybooksAPI(_VersionsFakeClient())
    with pytest.raises(ValueError, match="delete_version"):
        pb.delete_version("nope")


# -- diff_versions ----------------------------------------------------------
def test_diff_versions_finds_changed_step():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    # v2 differs from v1 only in step s2's arguments
    d = pb.diff_versions(_V1_UUID, _V2_UUID)
    assert isinstance(d, VersionDiff)
    assert d.is_clean is False
    assert any(c2.field == "arguments" and c2.step == "s2" for c2 in d.changed)
    assert d.added == [] and d.removed == []


def test_diff_versions_accepts_objects():
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    v1 = pb.get_version(_V1_UUID)
    v2 = pb.get_version(_V2_UUID)
    d = pb.diff_versions(v1, v2)
    assert d.is_clean is False


def test_diff_versions_refetches_object_without_json():
    # a create_version response object has json=None; diff must re-fetch it
    c = _VersionsFakeClient()
    pb = PlaybooksAPI(c)
    created = PlaybookVersion(uuid=_V1_UUID, note="v3")  # no json
    d = pb.diff_versions(created, _V2_UUID)
    assert d.is_clean is False


def test_diff_snapshots_clean_when_identical():
    a = {"steps": [{"uuid": "s1", "arguments": {"x": 1}}], "routes": [], "groups": []}
    assert _diff_snapshots(a, a).is_clean is True


def test_diff_snapshots_added_removed_changed():
    a = {
        "steps": [
            {"uuid": "s1", "name": "One", "stepType": {"name": "A"}, "arguments": {"x": 1}},
            {"uuid": "s2", "name": "Two", "stepType": {"name": "B"}, "arguments": {"y": 2}},
        ],
        "routes": [{"uuid": "r1"}],
        "groups": [{"uuid": "g1"}],
    }
    b = {
        "steps": [
            {"uuid": "s2", "name": "Two!", "stepType": {"name": "B"}, "arguments": {"y": 3}},
            {"uuid": "s3", "name": "Three", "stepType": {"name": "C"}, "arguments": {}},
        ],
        "routes": [{"uuid": "r2"}],
        "groups": [{"uuid": "g1"}],
    }
    d = _diff_snapshots(a, b)
    assert d.removed == ["s1"]
    assert d.added == ["s3"]
    assert d.routes_added == ["r2"]
    assert d.routes_removed == ["r1"]
    fields = {(c.step, c.field) for c in d.changed}
    assert ("s2", "arguments") in fields
    assert ("s2", "name") in fields
