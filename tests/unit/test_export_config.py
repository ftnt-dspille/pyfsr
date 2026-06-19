"""Unit tests for ExportConfigAPI — offline paths of the config-export surface.

Covers template resolution, payload construction, filename derivation, the
connector-export entry shape, and the poll→download happy path, all via a fake
client (no live box). Lookup-heavy methods are stubbed where a box would be
required.
"""

from types import SimpleNamespace

import pytest

from pyfsr.api.export_config import ExportConfigAPI


class FakeClient:
    def __init__(self, handler=None):
        self.calls = []
        self._handler = handler or (lambda *a, **k: {})
        self.auth = SimpleNamespace(check_operation_supported=lambda operation=None: None)

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append(("GET", url, params))
        return self._handler("GET", url, params=params, headers=headers)

    def post(self, url, data=None, **kw):
        self.calls.append(("POST", url, data))
        return self._handler("POST", url, data=data)

    def put(self, url, data=None, **kw):
        self.calls.append(("PUT", url, data))
        return self._handler("PUT", url, data=data)

    def delete(self, url, **kw):
        self.calls.append(("DELETE", url, None))
        return self._handler("DELETE", url)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("pyfsr.api.export_config.time.sleep", lambda *_: None)


def _api(handler=None):
    c = FakeClient(handler)
    return ExportConfigAPI(c), c


# ------------------------------------------------------------------- _trigger_export


def test_trigger_export_requires_zip_filename():
    api, _ = _api()
    with pytest.raises(ValueError, match="must end in .zip"):
        api._trigger_export("tmpl-1", "config.txt")


def test_trigger_export_builds_query():
    api, c = _api(lambda m, u, **k: {"jobUuid": "j1"})
    api._trigger_export("tmpl-1", "config.zip")
    method, url, _ = c.calls[-1]
    assert method == "PUT"
    assert url == "/api/export?fileName=config.zip&template=tmpl-1"


# ------------------------------------------------------------------- _get_template_uuid


def test_get_template_uuid_picks_most_recent_by_create_date():
    members = [
        {"name": "T", "@id": "/api/3/export_templates/old", "createDate": 100},
        {"name": "T", "@id": "/api/3/export_templates/new", "createDate": 200},
        {"name": "Other", "@id": "/api/3/export_templates/x", "createDate": 300},
    ]
    api, _ = _api(lambda m, u, **k: {"hydra:member": members})
    assert api._get_template_uuid("T") == "new"


def test_get_template_uuid_raises_when_missing():
    api, _ = _api(lambda m, u, **k: {"hydra:member": []})
    with pytest.raises(ValueError, match="Export template not found"):
        api._get_template_uuid("Nope")


# ------------------------------------------------------------------- _get_picklist_iri


def test_get_picklist_iri_returns_id():
    api, _ = _api(lambda m, u, **k: {"hydra:member": [{"@id": "/api/3/picklists/abc"}]})
    assert api._get_picklist_iri("Severity") == "/api/3/picklists/abc"


def test_get_picklist_iri_raises_when_missing():
    api, _ = _api(lambda m, u, **k: {"hydra:member": []})
    with pytest.raises(ValueError, match="Picklist not found"):
        api._get_picklist_iri("Nope")


# ------------------------------------------------------------------ create_export_template


def test_create_export_template_posts_full_payload():
    api, c = _api(lambda m, u, **k: {"@id": "/api/3/export_templates/t1"})
    api.create_export_template("My Tmpl", options={"connectors": []}, metadata={"x": 1})
    method, url, data = c.calls[-1]
    assert (method, url) == ("POST", "/api/3/export_templates")
    assert data == {"name": "My Tmpl", "options": {"connectors": []}, "metadata": {"x": 1}}


def test_create_export_template_defaults_metadata():
    api, c = _api(lambda m, u, **k: {"@id": "/x"})
    api.create_export_template("T", options={})
    assert c.calls[-1][2]["metadata"] == {"autoSelectPicklists": True}


# --------------------------------------------------------------- create_simplified_template


def test_create_simplified_template_builds_module_options():
    captured = {}

    def handler(m, u, **k):
        if m == "POST":
            captured["data"] = k.get("data")
        return {"@id": "/api/3/export_templates/t1"}

    api, _ = _api(handler)
    api.create_simplified_template(
        name="Alert Export",
        modules=["alerts"],
        module_attributes={"alerts": ["name", "status"]},
    )
    opts = captured["data"]["options"]
    assert opts["modules"] == [{"value": "alerts", "includedAttributes": ["name", "status"]}]
    # untouched sections are still present as empty scaffolding
    assert opts["connectors"] == []
    assert opts["picklistNames"] == []
    assert captured["data"]["metadata"] == {"autoSelectPicklists": True}


# ------------------------------------------------------------------- delete_template


def test_delete_template_issues_delete():
    api, c = _api()
    api.delete_template("tmpl-1")
    assert c.calls[-1] == ("DELETE", "/api/3/export_templates/tmpl-1", None)


# ------------------------------------------------------------------- export_by_template_name


def test_export_by_template_name_derives_filename(monkeypatch):
    api, _ = _api(lambda m, u, **k: {"hydra:member": [{"name": "Alert Cfg", "@id": "/x/u1"}]})
    seen = {}
    monkeypatch.setattr(
        api,
        "_export_with_template",
        lambda template_uuid, output_path, filename, poll_interval: (
            seen.update(uuid=template_uuid, filename=filename, output=output_path) or "out.zip"
        ),
    )
    api.export_by_template_name("Alert Cfg")
    assert seen["uuid"] == "u1"
    assert seen["filename"] == "alert_cfg.zip"  # lowercased, spaces -> underscores
    assert seen["output"] is None


# ------------------------------------------------------------------- export_connector


def _installed_connector(**over):
    rec = {
        "name": "code-snippet",
        "label": "Code Snippet",
        "version": "2.1.4",
        "system": True,
        "config_count": 2,
    }
    rec.update(over)
    return rec


def test_export_connector_builds_entry_and_cleans_up(monkeypatch):
    captured = {}

    def handler(m, u, **k):
        if m == "GET" and u.startswith("/api/integration/connectors/"):
            return {"data": [_installed_connector()]}
        if m == "POST":
            captured["template"] = k.get("data")
            return {"@id": "/api/3/export_templates/tmpl-1"}
        return {}

    api, c = _api(handler)
    monkeypatch.setattr(
        api, "_export_with_template", lambda **kw: kw.get("output_path") or "out.zip"
    )
    api.export_connector("code-snippet", output_path="cs.zip")

    entry = captured["template"]["options"]["connectors"][0]
    assert entry["value"] == "cyops-connector-code-snippet-2.1.4"
    assert entry["version"] == "2.1.4"
    assert entry["configurations"] is True
    assert entry["rpm"] is True  # system connector
    assert entry["configCount"] == 2
    # throwaway template deleted afterwards
    assert ("DELETE", "/api/3/export_templates/tmpl-1", None) in c.calls


def test_export_connector_not_installed_raises():
    api, _ = _api(lambda m, u, **k: {"data": []})
    with pytest.raises(ValueError, match="not installed"):
        api.export_connector("ghost")


def test_export_connector_keeps_template_when_cleanup_disabled(monkeypatch):
    def handler(m, u, **k):
        if m == "GET" and u.startswith("/api/integration/connectors/"):
            return {"data": [_installed_connector()]}
        if m == "POST":
            return {"@id": "/api/3/export_templates/tmpl-1"}
        return {}

    api, c = _api(handler)
    monkeypatch.setattr(api, "_export_with_template", lambda **kw: "out.zip")
    api.export_connector("code-snippet", output_path="cs.zip", cleanup_template=False)
    assert not any(m == "DELETE" for m, _, _ in c.calls)


# ----------------------------------------------------------- poll + download happy path


def test_export_with_template_polls_then_downloads(tmp_path):
    out = tmp_path / "config.zip"

    def handler(m, u, **k):
        if m == "PUT" and u.startswith("/api/export"):
            return {"jobUuid": "ejob-1"}
        if m == "GET" and u.startswith("/api/3/export_jobs/"):
            return {"status": "Export Complete", "file": {"@id": "/api/3/files/ef1"}}
        if m == "GET" and u == "/api/3/files/ef1":
            return b"ZIPBYTES"
        return {}

    api, _ = _api(handler)
    result = api._export_with_template("tmpl-1", output_path=str(out), poll_interval=0)
    assert result == str(out)
    assert out.read_bytes() == b"ZIPBYTES"
