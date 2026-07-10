"""Additional workflow-collection coverage: resolution, import validation,
file import, exists(), make-public, and the YAML source helper."""

import pytest

from pyfsr.api.workflow_collections import WorkflowCollectionsAPI, _read_yaml_source
from pyfsr.exceptions import ResourceNotFoundError


class RecordingClient:
    def __init__(self, responses=None, get_raises=False):
        self.calls = []
        self.responses = responses or {}
        self.get_raises = get_raises

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint, params))
        if self.get_raises:
            raise RuntimeError("boom")
        if endpoint in self.responses:
            return self.responses[endpoint]
        return {"hydra:member": [], "hydra:totalItems": 0}

    def post(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("POST", endpoint, data))
        return {"ok": True}

    def put(self, endpoint, data=None, params=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        return {"ok": True}

    def delete(self, endpoint, params=None, **kw):
        self.calls.append(("DELETE", endpoint, params))


UUID = "46a177c6-200c-425a-b16d-c52ebb915d6b"


# -- _resolve_collection -----------------------------------------------------
def test_resolve_by_uuid_fetches_directly():
    c = RecordingClient(responses={f"/api/3/workflow_collections/{UUID}": {"uuid": UUID, "name": "Pack"}})
    a = WorkflowCollectionsAPI(c)
    coll = a._resolve_collection(UUID)
    assert coll["uuid"] == UUID


def test_resolve_by_name_matches_single():
    c = RecordingClient(responses={"/api/3/workflow_collections": {"hydra:member": [{"uuid": "c-1", "name": "Pack"}]}})
    a = WorkflowCollectionsAPI(c)
    assert a._resolve_collection("Pack")["uuid"] == "c-1"


def test_resolve_by_name_not_found_raises():
    a = WorkflowCollectionsAPI(RecordingClient())
    with pytest.raises(ResourceNotFoundError):
        a._resolve_collection("Nope")


def test_resolve_by_name_ambiguous_raises():
    members = [{"uuid": "c-1", "name": "P"}, {"uuid": "c-2", "name": "P"}]
    dupes = {"/api/3/workflow_collections": {"hydra:member": members}}
    a = WorkflowCollectionsAPI(RecordingClient(responses=dupes))
    with pytest.raises(ValueError, match="disambiguate"):
        a._resolve_collection("P")


def test_resolve_empty_string_raises():
    a = WorkflowCollectionsAPI(RecordingClient())
    with pytest.raises(ValueError):
        a._resolve_collection("  ")


# -- import_export validation ------------------------------------------------
def test_import_export_rejects_non_dict():
    a = WorkflowCollectionsAPI(RecordingClient())
    with pytest.raises(ValueError, match="export envelope"):
        a.import_export(["not", "a", "dict"])


def test_import_export_requires_data_key():
    a = WorkflowCollectionsAPI(RecordingClient())
    with pytest.raises(ValueError, match="'data' key"):
        a.import_export({"type": "workflow_collections"})


# -- import_from_file --------------------------------------------------------
def test_import_from_file_missing_raises():
    a = WorkflowCollectionsAPI(RecordingClient())
    with pytest.raises(FileNotFoundError):
        a.import_from_file("/nope/export.json")


def test_import_from_file_invalid_json_raises(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json")
    a = WorkflowCollectionsAPI(RecordingClient())
    with pytest.raises(ValueError, match="not valid JSON"):
        a.import_from_file(f)


def test_import_from_file_valid_imports(tmp_path):
    f = tmp_path / "export.json"
    f.write_text('{"type": "workflow_collections", "data": [{"uuid": "c-1", "name": "Pack"}]}')
    c = RecordingClient()
    WorkflowCollectionsAPI(c).import_from_file(f)
    assert c.calls[-1] == ("POST", "/api/3/workflow_collections", {"uuid": "c-1", "name": "Pack"})


# -- exists() ----------------------------------------------------------------
def test_exists_rejects_bad_uuid():
    a = WorkflowCollectionsAPI(RecordingClient())
    with pytest.raises(ValueError):
        a.exists("not-a-uuid")


def test_exists_true_when_get_succeeds():
    c = RecordingClient(responses={f"/api/3/workflow_collections/{UUID}": {"uuid": UUID}})
    assert WorkflowCollectionsAPI(c).exists(UUID) is True


def test_exists_false_when_get_raises():
    assert WorkflowCollectionsAPI(RecordingClient(get_raises=True)).exists(UUID) is False


# -- _make_playbooks_public (via replace import) -----------------------------
def test_replace_makes_private_playbooks_public_before_delete():
    detail = {
        "uuid": UUID,
        "name": "Pack",
        "workflows": [
            {"uuid": "wf-priv", "isPrivate": True},
            {"uuid": "wf-pub", "isPrivate": False},
            {"isPrivate": True},  # no uuid — skipped
        ],
    }
    c = RecordingClient(responses={f"/api/3/workflow_collections/{UUID}": detail})
    a = WorkflowCollectionsAPI(c)
    a.import_export({"type": "workflow_collections", "data": [{"uuid": UUID, "name": "Pack"}]}, replace=True)
    # only the private workflow with a uuid gets a PUT flipping isPrivate False
    puts = [call for call in c.calls if call[0] == "PUT"]
    assert puts == [("PUT", "/api/3/workflows/wf-priv", {"isPrivate": False, "owners": []})]


def test_make_public_swallows_get_failure():
    a = WorkflowCollectionsAPI(RecordingClient(get_raises=True))
    # should not raise even though the detail GET fails
    a._make_playbooks_public(UUID)


def test_make_public_swallows_put_failure():
    detail = {"uuid": UUID, "workflows": [{"uuid": "wf-priv", "isPrivate": True}]}

    class PutFailsClient(RecordingClient):
        def put(self, endpoint, data=None, params=None, **kw):
            raise RuntimeError("put denied")

    c = PutFailsClient(responses={f"/api/3/workflow_collections/{UUID}": detail})
    # best-effort: the failing PUT is swallowed, no exception propagates
    WorkflowCollectionsAPI(c)._make_playbooks_public(UUID)


def test_replace_skips_delete_when_collection_absent():
    # uuid present in the export but not on the appliance: exists()->False, no delete
    c = RecordingClient(get_raises=True)  # exists() probe raises -> False, so delete is skipped
    a = WorkflowCollectionsAPI(c)
    a.import_export({"type": "workflow_collections", "data": [{"uuid": UUID, "name": "Pack"}]}, replace=True)
    assert not any(call[0] == "DELETE" for call in c.calls)
    assert c.calls[-1][0] == "POST"


# -- export_to_yaml / import_from_yaml (compiler mocked) ---------------------
def test_export_to_yaml_decompiles_resolved_collection(monkeypatch):
    import pyfsr.authoring as authoring

    captured = {}

    def fake_decompile(envelope, client=None, db_path=None):
        captured["envelope"] = envelope
        return "name: Pack\n"

    monkeypatch.setattr(authoring, "decompile_playbook_yaml", fake_decompile)
    c = RecordingClient(responses={f"/api/3/workflow_collections/{UUID}": {"uuid": UUID, "name": "Pack"}})
    out = WorkflowCollectionsAPI(c).export_to_yaml(UUID)
    assert out == "name: Pack\n"
    assert captured["envelope"]["type"] == "workflow_collections"
    assert captured["envelope"]["data"][0]["uuid"] == UUID


def test_import_from_yaml_strict_warnings_blocks(monkeypatch):
    a = WorkflowCollectionsAPI(RecordingClient())

    class FakeResult:
        blocking = []
        warnings = ["w1"]
        fsr_json = {"type": "workflow_collections", "data": []}

    monkeypatch.setattr(a, "compile_yaml", lambda *args, **kw: FakeResult())
    monkeypatch.setattr("pyfsr.authoring.format_diagnostic", lambda d: str(d))
    with pytest.raises(ValueError, match="failed to compile"):
        a.import_from_yaml("name: x\n", strict_warnings=True)


# -- _read_yaml_source -------------------------------------------------------
def test_read_yaml_from_path_object(tmp_path):
    f = tmp_path / "pb.yaml"
    f.write_text("name: x\n")
    assert _read_yaml_source(f) == "name: x\n"


def test_read_yaml_from_yaml_path_string(tmp_path):
    f = tmp_path / "pb.yaml"
    f.write_text("name: y\n")
    assert _read_yaml_source(str(f)) == "name: y\n"


def test_read_yaml_missing_path_string_raises():
    with pytest.raises(FileNotFoundError):
        _read_yaml_source("/nope/pb.yaml")


def test_read_yaml_raw_text_returned_as_is():
    raw = "name: z\nsteps: []\n"
    assert _read_yaml_source(raw) == raw


def test_read_yaml_bad_type_raises():
    with pytest.raises(TypeError):
        _read_yaml_source(123)
