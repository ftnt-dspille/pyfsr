"""Unit tests for WidgetsAPI (upload / publish / deploy wire shapes)."""

import pytest

from pyfsr.api.widgets import WidgetsAPI
from pyfsr.exceptions import APIError, WidgetPublishError, WidgetUploadConflict

_DEV_MANIFEST = {
    "hydra:member": [
        {
            "@id": "/api/3/widgets/abc-123",
            "uuid": "abc-123",
            "name": "my-widget",
            "version": "1.2.0",
            "title": "My Widget",
            "draft": True,
            "installed": False,
            "tree": {"huge": "layout blob"},
        }
    ]
}


class FakeClient:
    def __init__(self, *, get_map=None, post_resp=None, put_resp=None, post_raiser=None):
        self.base_url = "https://fsr.example.com"
        self.get_calls = []
        self.post_calls = []
        self.put_calls = []
        self.request_calls = []
        self._get_map = get_map or {}
        self._post_resp = post_resp
        self._put_resp = put_resp
        self._post_raiser = post_raiser

    def get(self, endpoint, params=None, **kwargs):
        self.get_calls.append((endpoint, params))
        return self._get_map.get(endpoint, {"hydra:member": []})

    def post(self, endpoint, data=None, files=None, params=None, **kwargs):
        self.post_calls.append((endpoint, data, files, params))
        if self._post_raiser:
            raise self._post_raiser
        return self._post_resp

    def put(self, endpoint, data=None, params=None, **kwargs):
        self.put_calls.append((endpoint, data, params))
        return self._put_resp

    def request(self, method, endpoint, data=None, params=None, **kwargs):
        self.request_calls.append((method, endpoint, data, params))
        return None


def _api(**kw):
    c = FakeClient(**kw)
    return WidgetsAPI(c), c


def _tgz(tmp_path) -> str:
    p = tmp_path / "my-widget-1.2.0.tgz"
    p.write_bytes(b"fake")
    return str(p)


# -- upload -------------------------------------------------------------
def test_upload_sends_type_widget_and_replace(tmp_path):
    api, client = _api(post_resp={"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": True})
    record = api.upload(_tgz(tmp_path), replace=True)
    endpoint, data, files, params = client.post_calls[0]
    assert endpoint == "/api/3/solutionpacks/install"
    assert params == {"$type": "widget", "$replace": "true"}
    assert "file" in files
    assert record.uuid == "abc-123"
    assert record.draft is True


def test_upload_no_replace_omits_query_flag(tmp_path):
    api, client = _api(post_resp={"uuid": "abc-123"})
    api.upload(_tgz(tmp_path), replace=False)
    _, _, _, params = client.post_calls[0]
    assert params == {"$type": "widget"}


def test_upload_missing_file_raises():
    api, _ = _api()
    with pytest.raises(FileNotFoundError):
        api.upload("/nope/does-not-exist.tgz")


def test_upload_conflict_maps_to_typed_error(tmp_path):
    err = APIError("Widget with Name - my-widget Version - 1.2.0 already exists in widget workspace.")
    api, _ = _api(post_raiser=err)
    with pytest.raises(WidgetUploadConflict):
        api.upload(_tgz(tmp_path), replace=False)


def test_upload_other_api_error_propagates(tmp_path):
    err = APIError("some other failure")
    api, _ = _api(post_raiser=err)
    with pytest.raises(APIError):
        api.upload(_tgz(tmp_path), replace=False)


# -- publish --------------------------------------------------------------
def test_publish_strips_tree_and_sets_flags():
    put_resp = {"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": False, "installed": True}
    api, client = _api(get_map={"/api/3/widgets/development/abc-123": _DEV_MANIFEST}, put_resp=put_resp)
    record = api.publish("abc-123", replace=True, go_live=True)

    endpoint, data, params = client.put_calls[0]
    assert endpoint == "/api/3/widgets/abc-123"
    assert "tree" not in data
    assert data["@id"] == "/api/3/widgets/abc-123"
    assert data["draft"] is False
    assert data["installed"] is True
    assert data["enablePublish"] is False
    assert data["replace"] is True
    assert data["replaceVersions"] == []
    assert isinstance(data["publishedDate"], int)
    assert record.installed is True
    assert record.draft is False


def test_publish_as_draft_sends_draft_true():
    api, client = _api(
        get_map={"/api/3/widgets/development/abc-123": _DEV_MANIFEST},
        put_resp={"uuid": "abc-123", "draft": True, "installed": True},
    )
    api.publish("abc-123", replace=True, go_live=False)
    _, data, _ = client.put_calls[0]
    assert data["draft"] is True


# -- deploy -----------------------------------------------------------------
def test_deploy_uploads_then_publishes_then_settles(tmp_path):
    settled = {
        "hydra:member": [
            {"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": False, "installed": True}
        ]
    }
    api, client = _api(
        post_resp={"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": True, "installed": False},
        get_map={
            "/api/3/widgets/development/abc-123": _DEV_MANIFEST,
            "/api/3/widgets": settled,
        },
        put_resp={"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": False, "installed": True},
    )
    record = api.deploy(_tgz(tmp_path), replace=True, wait=True, interval=0.01, timeout=1.0)
    assert record.published
    assert record.version == "1.2.0"


def test_deploy_wait_false_skips_settle_poll(tmp_path):
    api, client = _api(
        post_resp={"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": True, "installed": False},
        get_map={"/api/3/widgets/development/abc-123": _DEV_MANIFEST},
        put_resp={"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": False, "installed": True},
    )
    record = api.deploy(_tgz(tmp_path), wait=False)
    assert record.uuid == "abc-123"
    assert "/api/3/widgets" not in [c[0] for c in client.get_calls]


def test_deploy_raises_when_never_settles(tmp_path):
    api, client = _api(
        post_resp={"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": True, "installed": False},
        get_map={
            "/api/3/widgets/development/abc-123": _DEV_MANIFEST,
            "/api/3/widgets": {"hydra:member": []},
        },
        put_resp={"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": False, "installed": True},
    )
    with pytest.raises(WidgetPublishError):
        api.deploy(_tgz(tmp_path), wait=True, interval=0.01, timeout=0.05)


# -- list / get ---------------------------------------------------------
def test_list_filters_by_name_and_installed():
    members = {
        "hydra:member": [
            {"uuid": "a", "name": "w1", "version": "1.0.0", "installed": True, "draft": False},
            {"uuid": "b", "name": "w1", "version": "1.1.0", "installed": False, "draft": True},
            {"uuid": "c", "name": "w2", "version": "1.0.0", "installed": True, "draft": False},
        ]
    }
    api, _ = _api(get_map={"/api/3/widgets": members})
    assert {r.uuid for r in api.list(name="w1")} == {"a", "b"}
    assert {r.uuid for r in api.list(installed=True)} == {"a", "c"}


def test_get_returns_newest_version():
    members = {
        "hydra:member": [
            {"uuid": "a", "name": "w1", "version": "1.0.0"},
            {"uuid": "b", "name": "w1", "version": "1.10.0"},
            {"uuid": "c", "name": "w1", "version": "1.2.0"},
        ]
    }
    api, _ = _api(get_map={"/api/3/widgets": members})
    assert api.get("w1").uuid == "b"


def test_get_returns_none_for_unknown_name():
    api, _ = _api(get_map={"/api/3/widgets": {"hydra:member": []}})
    assert api.get("nope") is None


# -- remove -----------------------------------------------------------------
def test_remove_sends_delete_by_ids():
    api, client = _api()
    api.remove("abc-123")
    method, endpoint, data, _ = client.request_calls[0]
    assert method == "DELETE"
    assert endpoint == "/api/3/delete/widgets"
    assert data == {"ids": ["abc-123"]}
