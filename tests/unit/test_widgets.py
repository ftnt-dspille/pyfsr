"""Unit tests for WidgetsAPI (upload / publish / deploy wire shapes)."""

import json
import tarfile

import pytest

from pyfsr.api.widgets import WidgetsAPI
from pyfsr.exceptions import APIError, WidgetPublishError, WidgetUploadConflict
from pyfsr.widget_dev import pack_widget

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


# -- pack (widget folder -> .tgz) ---------------------------------------
def _widget_dir(tmp_path, *, name="my-widget", version="1.2.0", extra=None):
    d = tmp_path / "widget-src"
    d.mkdir()
    info = {"name": name, "version": version, "title": "My Widget"}
    (d / "info.json").write_text(json.dumps(info))
    for f in ("view.html", "edit.html", "view.controller.js", "edit.controller.js"):
        (d / f).write_text("// widget\n")
    assets = d / "widgetAssets" / "js"
    assets.mkdir(parents=True)
    (assets / "service.js").write_text("// asset\n")
    # Junk that must never reach the archive.
    (d / ".DS_Store").write_bytes(b"junk")
    (d / "._info.json").write_bytes(b"appledouble")
    (d / "node_modules").mkdir()
    (d / "node_modules" / "x.js").write_text("// dep\n")
    for path in extra or []:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    return d


def test_pack_widget_bundles_content_hub_layout(tmp_path):
    d = _widget_dir(tmp_path)
    out = pack_widget(str(d))
    assert out == str(tmp_path / "my-widget-1.2.0.tgz")

    with tarfile.open(out, "r:gz") as tar:
        members = tar.getmembers()
    tops = {m.name.split("/")[0] for m in members}
    assert tops == {"my-widget-1.2.0"}
    names = {m.name for m in members}
    assert "my-widget-1.2.0/info.json" in names
    assert "my-widget-1.2.0/view.controller.js" in names
    assert "my-widget-1.2.0/widgetAssets/js/service.js" in names
    # No junk, ever: AppleDouble entries break the importer's info.json walk.
    assert not any("._" in m or m.endswith(".DS_Store") for m in names)
    assert not any("node_modules" in m for m in names)
    # Numeric owner only, like the box's own packages.
    assert all(m.uid == 0 and m.gid == 0 and m.uname == "" and m.gname == "" for m in members)


def test_pack_widget_arcname_uses_info_json_not_folder_name(tmp_path):
    d = _widget_dir(tmp_path, name="fortiGuardThreatMap", version="1.0.0")
    d = d.rename(d.parent / "wip-copy")  # working folder named something else
    out = pack_widget(str(d))
    with tarfile.open(out, "r:gz") as tar:
        tops = {m.name.split("/")[0] for m in tar.getmembers()}
    assert tops == {"fortiGuardThreatMap-1.0.0"}
    assert out.endswith("fortiGuardThreatMap-1.0.0.tgz")


def test_pack_widget_out_may_be_a_directory(tmp_path):
    d = _widget_dir(tmp_path)
    dest = tmp_path / "dist"
    dest.mkdir()
    out = pack_widget(str(d), str(dest))
    assert out == str(dest / "my-widget-1.2.0.tgz")
    assert (dest / "my-widget-1.2.0.tgz").exists()


def test_pack_widget_missing_info_json_raises(tmp_path):
    d = tmp_path / "not-a-widget"
    d.mkdir()
    (d / "view.html").write_text("x")
    with pytest.raises(ValueError, match="no info.json"):
        pack_widget(str(d))


def test_pack_widget_missing_required_file_names_it(tmp_path):
    d = tmp_path / "partial-widget"
    d.mkdir()
    (d / "info.json").write_text('{"name": "w", "version": "1.0.0"}')
    (d / "view.html").write_text("x")
    with pytest.raises(ValueError, match="edit.html"):
        pack_widget(str(d))


def test_pack_widget_missing_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        pack_widget(str(tmp_path / "nope"))


# -- upload/deploy accept a folder ---------------------------------------
def test_upload_packs_a_folder_before_posting(tmp_path):
    d = _widget_dir(tmp_path)
    api, client = _api(post_resp={"uuid": "abc-123", "name": "my-widget", "version": "1.2.0", "draft": True})
    record = api.upload(str(d), replace=True)
    assert record.name == "my-widget"
    endpoint, _, files, params = client.post_calls[0]
    assert endpoint == "/api/3/solutionpacks/install"
    assert params == {"$type": "widget", "$replace": "true"}
    assert "file" in files


def test_ensure_deployed_reads_a_folder(tmp_path, monkeypatch):
    d = _widget_dir(tmp_path)
    api, client = _api(
        get_map={
            "/api/3/widgets": {
                "hydra:member": [
                    {
                        "uuid": "u-1",
                        "name": "my-widget",
                        "version": "1.0.0",
                        "draft": False,
                        "installed": True,
                        "published": False,
                    }
                ]
            }
        }
    )
    monkeypatch.setattr(api, "deploy", lambda path, **kw: "deployed")
    assert api.ensure_deployed(str(d)) == "deployed"  # version differs -> deploys
    assert client.get_calls  # looked up the existing record


def test_ensure_deployed_folder_already_live_skips(tmp_path):
    d = _widget_dir(tmp_path)  # my-widget 1.2.0
    api, _ = _api(
        get_map={
            "/api/3/widgets": {
                "hydra:member": [
                    {
                        "uuid": "u-1",
                        "name": "my-widget",
                        "version": "1.2.0",
                        "draft": False,
                        "installed": True,
                        "published": True,
                    }
                ]
            }
        }
    )
    calls = []
    api.deploy = lambda path, **kw: calls.append(path)  # must not fire
    out = api.ensure_deployed(str(d))
    assert out.name == "my-widget" and out.version == "1.2.0"
    assert calls == []
