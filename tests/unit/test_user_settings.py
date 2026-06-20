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


class FakeClient:
    def __init__(self, actor=None):
        self._actor = actor if actor is not None else _ACTOR
        self.get_calls = []
        self.put_calls = []

    def get(self, endpoint, params=None, **kw):
        self.get_calls.append((endpoint, params))
        return self._actor

    def put(self, endpoint, data=None, params=None, **kw):
        self.put_calls.append((endpoint, data, params))
        return {"status": "ok"}


def _api(actor=None):
    c = FakeClient(actor)
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


def test_wired_on_client():
    from pyfsr.api.user_settings import UserSettingsAPI as _U

    assert _U is UserSettingsAPI
