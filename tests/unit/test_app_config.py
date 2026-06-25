"""Tests for the application configuration (navigation and module visibility) API."""

import copy

import pytest


def _capture(monkeypatch, mock_response, json_data):
    """Capture request details while mocking the response with stateful GET/PUT handling."""
    captured = {}
    state = {"data": copy.deepcopy(json_data)}  # Mutable state for GET/PUT

    def mock_request(self, method, url, **kwargs):
        # Capture the request details including URL and method
        captured["method"] = method
        captured["url"] = url

        # For PUT requests, update internal state with the new data
        if method.upper() == "PUT":
            # The FortiSOAR client sends data via the 'json' kwarg
            json_body = kwargs.get("json")
            if json_body is not None:
                # Update our state with the new data
                state["data"] = copy.deepcopy(json_body)
            # Return a deep copy of the updated state
            return mock_response(json_data=copy.deepcopy(state["data"]))

        # For GET requests, return current state
        return mock_response(json_data=copy.deepcopy(state["data"]))

    monkeypatch.setattr("requests.Session.request", mock_request)
    return captured


# Sample application config response (resembles actual /api/views/1/app response)
_APP_CONFIG = {
    "id": "app",
    "type": "app",
    "config": {
        "header": {},
        "navigation": [
            {
                "title": "Alerts",
                "icon": "icon-alerts",
                "require": [],
                "state": {"name": "main.modules.list", "parameters": {"module": "alerts"}},
            },
            {
                "title": "Incidents",
                "icon": "icon-incidents",
                "require": [],
                "state": {"name": "main.modules.list", "parameters": {"module": "incidents"}},
            },
            {
                "title": "SLA Templates",
                "icon": "icon-sla",
                "require": {"module": "sla_templates", "action": "canRead"},
                "state": {"name": "main.modules.list", "parameters": {"module": "sla_templates"}},
            },
        ],
    },
}


def test_get_returns_full_config(mock_client, mock_response, monkeypatch):
    """get() returns the complete application configuration."""
    captured = _capture(monkeypatch, mock_response, _APP_CONFIG)
    config = mock_client.app_config.get()
    assert captured["url"].endswith("/api/views/1/app")
    assert captured["method"] == "GET"
    assert config["id"] == "app"
    assert config["type"] == "app"
    assert "config" in config


def test_get_navigation_returns_nav_array(mock_client, mock_response, monkeypatch):
    """get_navigation() returns just the navigation array."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    nav = mock_client.app_config.get_navigation()
    assert isinstance(nav, list)
    assert len(nav) == 3
    assert nav[0]["title"] == "Alerts"
    assert nav[1]["title"] == "Incidents"
    assert nav[2]["title"] == "SLA Templates"


def test_find_navigation_item_by_module(mock_client, mock_response, monkeypatch):
    """find_navigation_item(module=...) finds an item by module name."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    item = mock_client.app_config.find_navigation_item(module="alerts")
    assert item is not None
    assert item["title"] == "Alerts"
    assert item["state"]["parameters"]["module"] == "alerts"


def test_find_navigation_item_by_title(mock_client, mock_response, monkeypatch):
    """find_navigation_item(title=...) finds an item by title."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    item = mock_client.app_config.find_navigation_item(title="Incidents")
    assert item is not None
    assert item["state"]["parameters"]["module"] == "incidents"


def test_find_navigation_item_returns_none_if_not_found(mock_client, mock_response, monkeypatch):
    """find_navigation_item() returns None if no match is found."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    item = mock_client.app_config.find_navigation_item(module="nonexistent")
    assert item is None
    item = mock_client.app_config.find_navigation_item(title="Missing")
    assert item is None


def test_find_navigation_item_requires_at_least_one_arg(mock_client, mock_response, monkeypatch):
    """find_navigation_item() raises ValueError if neither module nor title is provided."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="requires module or title"):
        mock_client.app_config.find_navigation_item()


def test_find_navigation_item_gated_visibility(mock_client, mock_response, monkeypatch):
    """find_navigation_item() returns item with gated visibility (non-empty require)."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    item = mock_client.app_config.find_navigation_item(module="sla_templates")
    assert item is not None
    assert isinstance(item["require"], dict)
    assert item["require"]["module"] == "sla_templates"
    assert item["require"]["action"] == "canRead"


def test_update_navigation_replaces_array_and_puts(mock_client, mock_response, monkeypatch):
    """update_navigation() replaces the navigation array and PUTs the config."""
    captured = _capture(monkeypatch, mock_response, _APP_CONFIG)
    new_nav = [
        {
            "title": "Alerts",
            "icon": "icon-alerts",
            "require": [],
            "state": {"name": "main.modules.list", "parameters": {"module": "alerts"}},
        },
    ]
    config = mock_client.app_config.update_navigation(new_nav)
    assert captured["url"].endswith("/api/views/1/app")
    assert captured["method"] == "PUT"
    assert config["config"]["navigation"] == new_nav


def test_update_navigation_rejects_empty_list(mock_client, mock_response, monkeypatch):
    """update_navigation() rejects an empty items list."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="cannot be empty"):
        mock_client.app_config.update_navigation([])


def test_update_navigation_rejects_non_list(mock_client, mock_response, monkeypatch):
    """update_navigation() rejects items that is not a list."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="must be a list"):
        mock_client.app_config.update_navigation({"title": "Alerts"})  # type: ignore


def test_set_navigation_visibility_unrestricted(mock_client, mock_response, monkeypatch):
    """set_navigation_visibility() can make a module unrestricted."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    config = mock_client.app_config.set_navigation_visibility("sla_templates", require=[])
    # Verify the SLA Templates item now has empty require
    nav = config["config"]["navigation"]
    sla_item = next(x for x in nav if x["title"] == "SLA Templates")
    assert sla_item["require"] == []


def test_set_navigation_visibility_with_gate(mock_client, mock_response, monkeypatch):
    """set_navigation_visibility() can update the require gate."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    new_require = {"module": "incidents", "action": "canWrite"}
    config = mock_client.app_config.set_navigation_visibility("alerts", require=new_require)
    nav = config["config"]["navigation"]
    alerts_item = next(x for x in nav if x["title"] == "Alerts")
    assert alerts_item["require"] == new_require


def test_set_navigation_visibility_none_require_becomes_empty_list(mock_client, mock_response, monkeypatch):
    """set_navigation_visibility(require=None) sets require to []."""
    _capture(monkeypatch, mock_response, copy.deepcopy(_APP_CONFIG))
    config = mock_client.app_config.set_navigation_visibility("sla_templates", require=None)
    nav = config["config"]["navigation"]
    sla_item = next(x for x in nav if x["title"] == "SLA Templates")
    assert sla_item["require"] == []


def test_set_navigation_visibility_raises_for_missing_module(mock_client, mock_response, monkeypatch):
    """set_navigation_visibility() raises ValueError if module not found."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="No navigation item found"):
        mock_client.app_config.set_navigation_visibility("nonexistent", require=[])


def test_navigation_item_require_variants(mock_client, mock_response, monkeypatch):
    """Navigation items can have require as empty array or object with module/action."""
    _capture(monkeypatch, mock_response, copy.deepcopy(_APP_CONFIG))
    nav = mock_client.app_config.get_navigation()
    assert len(nav) >= 3, f"Expected at least 3 items, got {len(nav)}"
    alerts = nav[0]
    incidents = nav[1]
    sla = nav[2]
    # Alerts and incidents are unrestricted
    assert alerts["require"] == []
    assert incidents["require"] == []
    # SLA Templates is gated
    assert isinstance(sla["require"], dict)
    assert "module" in sla["require"]
    assert "action" in sla["require"]


def test_app_config_api_registered_on_client(mock_client):
    """AppConfigAPI is registered on the client as app_config."""
    from pyfsr.api.app_config import AppConfigAPI

    assert hasattr(mock_client, "app_config")
    assert isinstance(mock_client.app_config, AppConfigAPI)
