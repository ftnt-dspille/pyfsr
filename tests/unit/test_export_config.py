"""Unit tests for ExportConfigAPI — offline paths of the config-export surface.

Covers template resolution, payload construction, filename derivation, the
connector-export entry shape, and the poll→download happy path, all via a fake
client (no live box). Lookup-heavy methods are stubbed where a box would be
required.
"""

from types import SimpleNamespace

import pytest

from pyfsr import Query
from pyfsr.api.export_config import ExportConfigAPI, ExportTemplate


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
    monkeypatch.setattr(api, "_export_with_template", lambda **kw: kw.get("output_path") or "out.zip")
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


# --------------------------------------------------------------- ExportTemplate builder


def test_export_template_add_module_shape():
    tmpl = ExportTemplate("T").add_module("alerts", fields=["name", "status"])
    assert tmpl.build() == {"modules": [{"value": "alerts", "includedAttributes": ["name", "status"]}]}


def test_export_template_add_record_set_with_query():
    q = Query(module="alerts").eq("status", "Open")
    tmpl = ExportTemplate("T").add_record_set("alerts", query=q, limit=5000, include_correlations=True)
    rs = tmpl.build()["recordSets"][0]
    # live-verified wire shape: type/label/includeCorrelations/include/query
    assert rs["type"] == "alerts"
    assert rs["label"] == "alerts"
    assert rs["includeCorrelations"] is True
    assert rs["include"] is True
    # the query is Query.to_body() with the live-required limit injected
    assert rs["query"]["logic"] == "AND"
    assert rs["query"]["filters"] == q.to_body()["filters"]
    assert rs["query"]["limit"] == 5000  # export trigger


def test_export_template_record_set_injects_default_limit():
    # limit is the record-export trigger; a default is always present
    rs = ExportTemplate("T").add_record_set("alerts").build()["recordSets"][0]
    assert rs["query"]["limit"] == 1000


def test_export_template_record_set_no_query_matches_all():
    tmpl = ExportTemplate("T").add_record_set("incidents", label="All incidents", limit=50)
    rs = tmpl.build()["recordSets"][0]
    assert rs["label"] == "All incidents"
    assert rs["query"] == {"logic": "AND", "filters": [], "limit": 50}
    assert rs["includeCorrelations"] is False


def test_export_template_accepts_raw_query_dict():
    raw = {"logic": "OR", "filters": [{"field": "severity", "operator": "eq", "value": "High"}]}
    tmpl = ExportTemplate("T").add_record_set("alerts", query=raw, limit=10)
    q = tmpl.build()["recordSets"][0]["query"]
    assert q["logic"] == "OR"
    assert q["filters"] == raw["filters"]
    assert q["limit"] == 10
    # the caller's dict is not mutated in place
    assert "limit" not in raw


def test_export_template_build_omits_empty_categories():
    # a freshly-built template with nothing added yields an empty options dict
    assert ExportTemplate("T").build() == {}
    # metadata carries the autoSelectPicklists flag
    assert ExportTemplate("T").metadata == {"autoSelectPicklists": True}
    assert ExportTemplate("T", auto_select_picklists=False).metadata == {"autoSelectPicklists": False}


def test_export_template_chaining_returns_self():
    tmpl = ExportTemplate("T")
    assert tmpl.add_module("alerts") is tmpl
    assert tmpl.add_record_set("alerts") is tmpl


# --------------------------------------------------------------- create_template / export_record_data


def test_create_template_posts_builder_payload():
    api, c = _api(lambda m, u, **k: {"@id": "/api/3/export_templates/t1"})
    tmpl = ExportTemplate("Open alerts").add_record_set("alerts", query=Query(module="alerts").eq("status", "Open"))
    api.create_template(tmpl)
    method, url, data = c.calls[-1]
    assert (method, url) == ("POST", "/api/3/export_templates")
    assert data["name"] == "Open alerts"
    assert data["options"]["recordSets"][0]["type"] == "alerts"
    assert data["metadata"] == {"autoSelectPicklists": True}


def test_export_record_data_builds_recordset_and_cleans_up(monkeypatch):
    captured = {}

    def handler(m, u, **k):
        if m == "POST":
            captured["template"] = k.get("data")
            return {"@id": "/api/3/export_templates/rs-1"}
        return {}

    api, c = _api(handler)
    monkeypatch.setattr(api, "_export_with_template", lambda **kw: kw.get("output_path") or "out.zip")
    api.export_record_data(
        "alerts",
        query=Query(module="alerts").eq("status", "Open"),
        include_correlations=True,
        output_path="alerts.zip",
    )

    rs = captured["template"]["options"]["recordSets"][0]
    assert rs["type"] == "alerts"
    assert rs["includeCorrelations"] is True
    assert rs["query"]["filters"][0]["value"] == "Open"
    assert rs["query"]["limit"] == 1000  # default export trigger present
    # throwaway template deleted afterwards
    assert ("DELETE", "/api/3/export_templates/rs-1", None) in c.calls


def test_export_record_data_keeps_template_when_cleanup_disabled(monkeypatch):
    api, c = _api(lambda m, u, **k: {"@id": "/api/3/export_templates/rs-1"})
    monkeypatch.setattr(api, "_export_with_template", lambda **kw: "out.zip")
    api.export_record_data("alerts", output_path="a.zip", cleanup_template=False)
    assert not any(m == "DELETE" for m, _, _ in c.calls)


# --------------------------------------------------- name-based categories (view/picklist/connector/collection)


def test_export_template_view_template_is_offline():
    tmpl = ExportTemplate("T").add_view_template("modules-alerts-list")
    assert tmpl.build()["viewTemplates"] == ["modules-alerts-list"]
    assert tmpl.needs_resolution is False


def test_export_template_needs_resolution_flag():
    assert ExportTemplate("T").add_picklist("AlertStatus").needs_resolution is True
    assert ExportTemplate("T").add_connector("OpenAI").needs_resolution is True
    assert ExportTemplate("T").add_playbook_collection("IR").needs_resolution is True
    # offline-only categories never need a lookup
    assert ExportTemplate("T").add_module("alerts").add_view_template("v").needs_resolution is False


def test_create_template_resolves_picklist_names():
    def handler(m, u, **k):
        if m == "GET" and u == "/api/3/picklist_names":
            return {"hydra:member": [{"@id": f"/api/3/picklists/{k['params']['name']}"}]}
        return {"@id": "/api/3/export_templates/t1"}

    api, c = _api(handler)
    tmpl = ExportTemplate("PL").add_picklist("AlertStatus").add_picklist("AlertSeverity")
    api.create_template(tmpl)
    opts = c.calls[-1][2]["options"]
    assert opts["picklistNames"] == ["/api/3/picklists/AlertStatus", "/api/3/picklists/AlertSeverity"]


def test_create_template_resolves_connector_entry(monkeypatch):
    api, c = _api(lambda m, u, **k: {"@id": "/api/3/export_templates/t1"})
    monkeypatch.setattr(
        api,
        "_get_connector_info",
        lambda name: {"label": "OpenAI", "value": "cyops-connector-openai-1.0.0", "version": "1.0.0"},
    )
    tmpl = ExportTemplate("C").add_connector("OpenAI", include_configurations=False)
    api.create_template(tmpl)
    entry = c.calls[-1][2]["options"]["connectors"][0]
    assert entry["value"] == "cyops-connector-openai-1.0.0"
    assert entry["version"] == "1.0.0"
    assert entry["configurations"] is False  # flag honored
    assert entry["include"] is True


def test_create_template_resolves_playbook_collection(monkeypatch):
    api, c = _api(lambda m, u, **k: {"@id": "/api/3/export_templates/t1"})
    monkeypatch.setattr(
        api,
        "_get_playbook_collection_info",
        lambda name: {"label": "Incident Response", "value": "coll-uuid"},
    )
    tmpl = ExportTemplate("PB").add_playbook_collection("Incident Response", include_versions=False)
    api.create_template(tmpl)
    pb = c.calls[-1][2]["options"]["playbooks"]
    coll = pb["collections"][0]
    assert coll["value"] == "coll-uuid"
    assert coll["includeVersions"] is False
    assert coll["includeGlobalVariables"] is True
    assert pb["globalVariables"] == []


def test_export_template_dashboards_and_widgets_are_offline():
    # id/name-based UI categories the engine takes verbatim — no lookup.
    tmpl = ExportTemplate("T").add_dashboard("dash-uuid").add_widget("myWidget")
    built = tmpl.build()
    assert built["dashboards"] == ["dash-uuid"]
    assert built["widgets"] == ["myWidget"]
    assert tmpl.needs_resolution is False


def test_export_template_role_needs_resolution():
    assert ExportTemplate("T").add_role("Full App Permissions").needs_resolution is True


def test_create_template_resolves_role_entry():
    def handler(m, u, **k):
        if m == "GET" and u == "/api/3/roles":
            return {
                "hydra:member": [
                    {
                        "@id": "/api/3/roles/role-uuid",
                        "name": k["params"]["name"],
                        "uuid": "role-uuid",
                        "label": "Full App Permissions",
                    }
                ]
            }
        return {"@id": "/api/3/export_templates/t1"}

    api, c = _api(handler)
    tmpl = ExportTemplate("R").add_role("Full App Permissions")
    api.create_template(tmpl)
    entry = c.calls[-1][2]["options"]["roles"][0]
    # live-verified wire shape: value is the role IRI the engine keys on.
    assert entry["value"] == "/api/3/roles/role-uuid"
    assert entry["name"] == "Full App Permissions"
    assert entry["uuid"] == "role-uuid"
    assert entry["label"] == "Full App Permissions"
    assert entry["include"] is True


def test_create_template_role_not_found_raises():
    api, _ = _api(lambda m, u, **k: {"hydra:member": []} if m == "GET" else {"@id": "/x"})
    with pytest.raises(ValueError, match="role 'Nope' not found"):
        api.create_template(ExportTemplate("R").add_role("Nope"))


def test_create_template_resolves_team_entry():
    def handler(m, u, **k):
        if m == "GET" and u == "/api/3/teams":
            return {
                "hydra:member": [{"@id": "/api/3/teams/team-uuid", "name": k["params"]["name"], "uuid": "team-uuid"}]
            }
        return {"@id": "/api/3/export_templates/t1"}

    api, c = _api(handler)
    api.create_template(ExportTemplate("T").add_team("SOC Team"))
    entry = c.calls[-1][2]["options"]["teams"][0]
    assert entry["value"] == "/api/3/teams/team-uuid"
    assert entry["name"] == "SOC Team"
    assert entry["include"] is True


def test_create_template_resolves_actor_by_title_client_side():
    # /api/3/actors takes no title filter; resolution matches the list client-side.
    def handler(m, u, **k):
        if m == "GET" and u == "/api/3/actors":
            return {
                "hydra:member": [
                    {"@id": "/api/3/people/other", "title": "Someone Else", "uuid": "other"},
                    {"@id": "/api/3/people/admin-uuid", "title": "Admin", "uuid": "admin-uuid"},
                ]
            }
        return {"@id": "/api/3/export_templates/t1"}

    api, c = _api(handler)
    api.create_template(ExportTemplate("A").add_actor("Admin"))
    entry = c.calls[-1][2]["options"]["actors"][0]
    # actors are people: value is a /api/3/people IRI, keyed on title.
    assert entry["value"] == "/api/3/people/admin-uuid"
    assert entry["title"] == "Admin"
    assert entry["uuid"] == "admin-uuid"


def test_create_template_actor_not_found_raises():
    api, _ = _api(lambda m, u, **k: {"hydra:member": []} if m == "GET" else {"@id": "/x"})
    with pytest.raises(ValueError, match="actor 'Ghost' not found"):
        api.create_template(ExportTemplate("A").add_actor("Ghost"))


def test_create_template_offline_only_skips_lookups():
    # no GET lookups fire when nothing name-based was added
    api, c = _api(lambda m, u, **k: {"@id": "/api/3/export_templates/t1"})
    tmpl = ExportTemplate("M").add_module("alerts").add_record_set("alerts")
    api.create_template(tmpl)
    assert not any(m == "GET" for m, _, _ in c.calls)
    opts = c.calls[-1][2]["options"]
    assert set(opts) == {"modules", "recordSets"}  # no empty picklistNames/connectors scaffolding
