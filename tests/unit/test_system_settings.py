"""Unit tests for SystemSettingsAPI — focused on the dev-mode toggles."""

import pytest

from pyfsr.api.system_settings import SystemSettingsAPI

_DEV_RECORD = {
    "uuid": "dev-uuid",
    "name": "Advanced Development Settings",
    "privateValues": {
        "values": [
            {
                "allowCustomConnector": False,
                "allowCustomWidget": False,
                "allow_ai_agent": True,
                "lastModifiedBy": {"id": 3},
            }
        ]
    },
}


class FakeClient:
    def __init__(self):
        self.put_calls = []

    def get(self, endpoint, params=None, **kw):
        return {"hydra:member": [{"name": "root", "parent": None}, _DEV_RECORD]}

    def put(self, endpoint, data=None, params=None, **kw):
        self.put_calls.append((endpoint, data, params))
        return {"uuid": "dev-uuid", **data}


def _api():
    c = FakeClient()
    return SystemSettingsAPI(c), c


def test_get_named_finds_record():
    api, _ = _api()
    assert api.get_named("Advanced Development Settings")["uuid"] == "dev-uuid"


def test_get_named_missing_raises():
    api, _ = _api()
    with pytest.raises(ValueError, match="No system_settings record named"):
        api.get_named("Nope")


def test_get_development_mode_maps_flags():
    api, _ = _api()
    assert api.get_development_mode() == {
        "connectors": False,
        "widgets": False,
        "agents": True,
    }


def test_set_development_mode_flips_only_given_flags():
    api, client = _api()
    api.set_development_mode(connectors=True, widgets=True)
    endpoint, body, params = client.put_calls[0]
    assert endpoint == "/api/3/system_settings/dev-uuid"
    assert params == {"$relationships": "true"}
    entry = body["privateValues"]["values"][0]
    assert entry["allowCustomConnector"] is True
    assert entry["allowCustomWidget"] is True
    assert entry["allow_ai_agent"] is True  # untouched (agents not passed)
    assert entry["lastModifiedBy"] == {"id": 3}  # preserved


def test_set_development_mode_requires_a_flag():
    api, _ = _api()
    with pytest.raises(ValueError, match="at least one flag"):
        api.set_development_mode()
