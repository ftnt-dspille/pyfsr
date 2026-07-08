"""Unit tests for UserSettingsAPI — read via actors/current, write via /current/<key>."""

import pytest

from pyfsr.api.user_settings import UserSettingsAPI

_ACTOR = {
    "@id": "/api/3/people/me",
    "name": "csadmin",
    "@settings": {
        "theme": "dark",
        "grid": {"alerts": {"columns": ["name", "severity"]}},
    },
}


class FakeViewTemplates:
    def __init__(self, templates=None):
        self._templates = templates or []
        self.list_calls = []

    def list_templates(self, module=None):
        self.list_calls.append(module)
        return [t for t in self._templates if module is None or t.get("module") == module]


class FakeClient:
    def __init__(self, actor=None, templates=None):
        self._actor = actor if actor is not None else _ACTOR
        self.get_calls = []
        self.put_calls = []
        self.delete_calls = []
        self.direct_get_return = None
        self.view_templates = FakeViewTemplates(templates)

    def get(self, endpoint, params=None, **kw):
        self.get_calls.append((endpoint, params))
        if endpoint == "/api/3/actors/current":
            return self._actor
        return self.direct_get_return

    def put(self, endpoint, data=None, params=None, **kw):
        self.put_calls.append((endpoint, data, params))
        return {"status": "ok"}

    def delete(self, endpoint, **kw):
        self.delete_calls.append(endpoint)
        return None


def _api(actor=None, templates=None):
    c = FakeClient(actor, templates)
    return UserSettingsAPI(c), c


def test_all_returns_settings_blob():
    api, c = _api()
    assert api.all() == _ACTOR["@settings"]
    assert c.get_calls == [("/api/3/actors/current", None)]


def test_all_empty_when_no_settings():
    api, _ = _api(actor={"name": "csadmin"})
    assert api.all() == {}


def test_get_top_level_key():
    api, _ = _api()
    assert api.get("theme") == "dark"


def test_get_nested_key_with_slash():
    api, _ = _api()
    assert api.get("grid/alerts") == {"columns": ["name", "severity"]}


def test_get_missing_key_returns_default():
    api, _ = _api()
    assert api.get("grid/incidents", default="fallback") == "fallback"


def test_get_no_key_returns_whole_blob():
    api, _ = _api()
    assert api.get() == _ACTOR["@settings"]


def test_set_uses_current_path_and_raw_body():
    api, c = _api()
    api.set("grid/alerts", {"columns": ["name"]})
    endpoint, data, _params = c.put_calls[0]
    assert endpoint == "/api/3/user_settings/current/grid/alerts"
    assert data == {"columns": ["name"]}


def test_set_accepts_scalar_value():
    api, c = _api()
    api.set("theme", "light")
    endpoint, data, _ = c.put_calls[0]
    assert endpoint == "/api/3/user_settings/current/theme"
    assert data == "light"


def test_set_empty_key_raises():
    api, _ = _api()
    with pytest.raises(ValueError):
        api.set("", {"x": 1})


def test_get_direct_hits_current_key_endpoint():
    api, c = _api()
    c.direct_get_return = "light"
    assert api.get_direct("theme") == "light"
    assert c.get_calls[-1] == ("/api/3/user_settings/current/theme", None)


def test_get_direct_empty_key_raises():
    api, _ = _api()
    with pytest.raises(ValueError):
        api.get_direct("")


def test_delete_uses_current_path():
    api, c = _api()
    api.delete("grid/alerts")
    assert c.delete_calls == ["/api/3/user_settings/current/grid/alerts"]


def test_delete_empty_key_raises():
    api, _ = _api()
    with pytest.raises(ValueError):
        api.delete("")


_TPL_UUID = "d77cd7b5-3e0b-43b5-8c9b-54651dacdebe"
_TPL_ROW = {"uuid": _TPL_UUID, "name": "CrowdStrike", "module": "alerts", "viewOptions": "detail"}


def test_view_template_convenience_roundtrip():
    api, c = _api(
        actor={"@settings": {"user": {"view": {"details": {"alerts": {"viewTemplate": _TPL_UUID}}}}}},
        templates=[_TPL_ROW],
    )
    assert api.get_view_template("alerts") == _TPL_UUID
    assert api.get_view_template("incidents", default=None) is None

    api.set_view_template("alerts", _TPL_UUID)  # already-a-uuid path: no lookup needed
    endpoint, data, _ = c.put_calls[-1]
    assert endpoint == "/api/3/user_settings/current/user/view/details/alerts/viewTemplate"
    assert data == _TPL_UUID

    api.clear_view_template("alerts")
    assert c.delete_calls[-1] == "/api/3/user_settings/current/user/view/details/alerts/viewTemplate"


def test_resolve_view_template_by_name_case_insensitive():
    api, c = _api(templates=[_TPL_ROW])
    assert api.resolve_view_template("alerts", "crowdstrike") == _TPL_UUID
    assert c.view_templates.list_calls == ["alerts"]


def test_resolve_view_template_uuid_passthrough_skips_lookup():
    api, c = _api(templates=[_TPL_ROW])
    assert api.resolve_view_template("alerts", _TPL_UUID) == _TPL_UUID
    assert c.view_templates.list_calls == []  # no lookup needed


def test_resolve_view_template_unknown_name_raises():
    api, _ = _api(templates=[_TPL_ROW])
    with pytest.raises(ValueError):
        api.resolve_view_template("alerts", "Nonexistent Template")


def test_resolve_view_template_scopes_by_layout_kind():
    # Regression: "Default Layout" is not unique across layouts — every module
    # ships one per viewOptions. Matching by name alone (ignoring viewOptions)
    # previously resolved to whichever layout listed first, silently wrong.
    form_row = {
        "uuid": "00e011c1-d777-4313-a21a-0fc24684d710",
        "name": "Default Layout",
        "module": "alerts",
        "viewOptions": "form",
    }
    detail_row = {
        "uuid": "bcfe8c15-5fd5-4d73-af64-ba0cb6c89d73",
        "name": "Default Layout",
        "module": "alerts",
        "viewOptions": "detail",
    }
    api, _ = _api(templates=[form_row, detail_row])
    assert api.resolve_view_template("alerts", "Default Layout") == detail_row["uuid"]
    assert api.resolve_view_template("alerts", "Default Layout", kind="form") == form_row["uuid"]


def test_set_view_template_by_name_resolves_then_writes_uuid():
    api, c = _api(templates=[_TPL_ROW])
    api.set_view_template("alerts", "CrowdStrike")
    endpoint, data, _ = c.put_calls[-1]
    assert endpoint == "/api/3/user_settings/current/user/view/details/alerts/viewTemplate"
    assert data == _TPL_UUID


def test_get_view_template_name_resolves_stored_uuid():
    api, _ = _api(
        actor={"@settings": {"user": {"view": {"details": {"alerts": {"viewTemplate": _TPL_UUID}}}}}},
        templates=[_TPL_ROW],
    )
    assert api.get_view_template_name("alerts") == "CrowdStrike"


def test_get_view_template_name_no_template_set_returns_default():
    api, _ = _api(actor={"@settings": {}}, templates=[_TPL_ROW])
    assert api.get_view_template_name("alerts", default="none") == "none"


def test_get_view_template_name_stale_uuid_returns_default():
    api, _ = _api(
        actor={"@settings": {"user": {"view": {"details": {"alerts": {"viewTemplate": "no-such-uuid"}}}}}},
        templates=[_TPL_ROW],
    )
    assert api.get_view_template_name("alerts", default="none") == "none"


def test_wired_on_client():
    from pyfsr.api.user_settings import UserSettingsAPI as _U

    assert _U is UserSettingsAPI
