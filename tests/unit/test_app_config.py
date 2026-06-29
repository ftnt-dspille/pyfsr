"""Tests for the application configuration (navigation and module visibility) API."""

import copy

import pytest

from pyfsr.models import NavItem, NavRequire, NavState


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


# Sample application config response (resembles actual /api/views/1/app response).
# Includes a top-level group ("Incident Response") to exercise nested search/insert.
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
                "title": "Incident Response",
                "icon": "icon-ir",
                "items": [
                    {
                        "title": "Incidents",
                        "icon": "icon-incidents",
                        "require": [],
                        "state": {"name": "main.modules.list", "parameters": {"module": "incidents"}},
                    },
                ],
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
    """get() returns the complete application configuration document (raw dict)."""
    captured = _capture(monkeypatch, mock_response, _APP_CONFIG)
    config = mock_client.app_config.get()
    assert captured["url"].endswith("/api/views/1/app")
    assert captured["method"] == "GET"
    assert config["id"] == "app"
    assert config["type"] == "app"
    assert "config" in config


def test_get_navigation_returns_typed_items(mock_client, mock_response, monkeypatch):
    """get_navigation() returns a list of typed NavItem (groups parsed recursively)."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    nav = mock_client.app_config.get_navigation()
    assert all(isinstance(x, NavItem) for x in nav)
    assert [x.title for x in nav] == ["Alerts", "Incident Response", "SLA Templates"]
    group = nav[1]
    assert group.is_group
    assert isinstance(group.items[0], NavItem)
    assert group.items[0].title == "Incidents"


def test_find_navigation_item_by_module(mock_client, mock_response, monkeypatch):
    """find_navigation_item(module=...) finds an item by module name, including nested."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    item = mock_client.app_config.find_navigation_item(module="alerts")
    assert isinstance(item, NavItem)
    assert item.title == "Alerts"
    assert item.state.parameters["module"] == "alerts"
    # nested leaf inside the "Incident Response" group
    nested = mock_client.app_config.find_navigation_item(module="incidents")
    assert nested is not None and nested.title == "Incidents"


def test_find_navigation_item_by_title(mock_client, mock_response, monkeypatch):
    """find_navigation_item(title=...) finds an item by title."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    item = mock_client.app_config.find_navigation_item(title="Incidents")
    assert item is not None
    assert item.state.parameters["module"] == "incidents"


def test_find_navigation_item_returns_none_if_not_found(mock_client, mock_response, monkeypatch):
    """find_navigation_item() returns None if no match is found."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    assert mock_client.app_config.find_navigation_item(module="nonexistent") is None
    assert mock_client.app_config.find_navigation_item(title="Missing") is None


def test_find_navigation_item_requires_at_least_one_arg(mock_client, mock_response, monkeypatch):
    """find_navigation_item() raises ValueError if neither module nor title is provided."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="requires module or title"):
        mock_client.app_config.find_navigation_item()


def test_find_navigation_item_gated_visibility(mock_client, mock_response, monkeypatch):
    """find_navigation_item() returns item with gated visibility (NavRequire)."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    item = mock_client.app_config.find_navigation_item(module="sla_templates")
    assert item is not None
    assert isinstance(item.require, NavRequire)
    assert item.require.module == "sla_templates"
    assert item.require.action == "canRead"


def test_update_navigation_replaces_array_and_puts(mock_client, mock_response, monkeypatch):
    """update_navigation() serializes NavItems, replaces the array, and PUTs the config."""
    captured = _capture(monkeypatch, mock_response, _APP_CONFIG)
    new_nav = [
        NavItem(
            title="Alerts",
            icon="icon-alerts",
            require=[],
            state=NavState(name="main.modules.list", parameters={"module": "alerts"}),
        ),
    ]
    config = mock_client.app_config.update_navigation(new_nav)
    assert captured["url"].endswith("/api/views/1/app")
    assert captured["method"] == "PUT"
    nav = config["config"]["navigation"]
    assert len(nav) == 1
    assert nav[0]["title"] == "Alerts"
    assert nav[0]["require"] == []


def test_update_navigation_rejects_empty_list(mock_client, mock_response, monkeypatch):
    """update_navigation() rejects an empty items list."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="cannot be empty"):
        mock_client.app_config.update_navigation([])


def test_add_navigation_item_top_level_bottom(mock_client, mock_response, monkeypatch):
    """add_navigation_item() appends to the top-level bar by default."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    leaf = NavItem(
        title="My Module",
        icon="icon-bookmark",
        state=NavState(name="main.modules.list", parameters={"module": "my_module"}),
        require=NavRequire(module="my_module", action="read"),
    )
    config = mock_client.app_config.add_navigation_item(leaf)
    nav = config["config"]["navigation"]
    assert nav[-1]["title"] == "My Module"
    assert nav[-1]["require"] == {"module": "my_module", "action": "read"}


def test_add_navigation_item_top_level_top(mock_client, mock_response, monkeypatch):
    """add_navigation_item(position='top') prepends to the top-level bar."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    leaf = NavItem(title="My Module", icon="icon-bookmark")
    config = mock_client.app_config.add_navigation_item(leaf, position="top")
    assert config["config"]["navigation"][0]["title"] == "My Module"


def test_add_navigation_item_inside_group_by_title(mock_client, mock_response, monkeypatch):
    """add_navigation_item(parent=<group title>) inserts inside that group's items."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    leaf = NavItem(title="My Module", icon="icon-bookmark")
    config = mock_client.app_config.add_navigation_item(leaf, parent="Incident Response", position="top")
    group = next(x for x in config["config"]["navigation"] if x.get("title") == "Incident Response")
    assert group["items"][0]["title"] == "My Module"


def test_add_navigation_item_parent_by_module_nests_under_leaf(mock_client, mock_response, monkeypatch):
    """add_navigation_item(parent=<module>) nests under the leaf bound to that module."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    leaf = NavItem(title="My Module", icon="icon-bookmark")
    config = mock_client.app_config.add_navigation_item(leaf, parent="incidents")
    group = next(x for x in config["config"]["navigation"] if x.get("title") == "Incident Response")
    incidents = next(x for x in group["items"] if x.get("title") == "Incidents")
    assert incidents["items"][-1]["title"] == "My Module"


def test_add_navigation_item_rejects_bad_position(mock_client, mock_response, monkeypatch):
    """add_navigation_item() rejects an invalid position."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="position must be"):
        mock_client.app_config.add_navigation_item(NavItem(title="X"), position="middle")  # type: ignore


def test_add_navigation_item_rejects_missing_parent(mock_client, mock_response, monkeypatch):
    """add_navigation_item() raises if the named parent group does not exist."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="No navigation group found"):
        mock_client.app_config.add_navigation_item(NavItem(title="X"), parent="DoesNotExist")


def test_remove_navigation_item_top_level(mock_client, mock_response, monkeypatch):
    """remove_navigation_item() removes a top-level entry and commits."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    config = mock_client.app_config.remove_navigation_item(title="Alerts")
    titles = [x["title"] for x in config["config"]["navigation"]]
    assert "Alerts" not in titles
    assert titles == ["Incident Response", "SLA Templates"]


def test_remove_navigation_item_nested(mock_client, mock_response, monkeypatch):
    """remove_navigation_item() descends into groups (by module name)."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    config = mock_client.app_config.remove_navigation_item(module="incidents")
    group = next(x for x in config["config"]["navigation"] if x["title"] == "Incident Response")
    assert group.get("items", []) == []


def test_remove_navigation_item_missing_ok_noop(mock_client, mock_response, monkeypatch):
    """remove_navigation_item() returns None (no PUT) when nothing matches and missing_ok."""
    captured = _capture(monkeypatch, mock_response, _APP_CONFIG)
    result = mock_client.app_config.remove_navigation_item(module="nonexistent")
    assert result is None
    assert captured["method"] == "GET"  # no PUT issued


def test_remove_navigation_item_missing_raises(mock_client, mock_response, monkeypatch):
    """remove_navigation_item(missing_ok=False) raises when nothing matches."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="no navigation item matched"):
        mock_client.app_config.remove_navigation_item(module="nonexistent", missing_ok=False)


def test_remove_navigation_item_requires_arg(mock_client, mock_response, monkeypatch):
    """remove_navigation_item() requires module or title."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    with pytest.raises(ValueError, match="requires module or title"):
        mock_client.app_config.remove_navigation_item()


def test_set_navigation_visibility_unrestricted(mock_client, mock_response, monkeypatch):
    """set_navigation_visibility() can make a module unrestricted."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    config = mock_client.app_config.set_navigation_visibility("sla_templates", require=[])
    nav = config["config"]["navigation"]
    sla_item = next(x for x in nav if x["title"] == "SLA Templates")
    assert sla_item["require"] == []


def test_set_navigation_visibility_with_gate(mock_client, mock_response, monkeypatch):
    """set_navigation_visibility() can update the require gate (dict or NavRequire)."""
    _capture(monkeypatch, mock_response, _APP_CONFIG)
    config = mock_client.app_config.set_navigation_visibility(
        "alerts", require={"module": "incidents", "action": "canWrite"}
    )
    nav = config["config"]["navigation"]
    alerts_item = next(x for x in nav if x["title"] == "Alerts")
    assert alerts_item["require"] == {"module": "incidents", "action": "canWrite"}


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


def test_app_config_api_registered_on_client(mock_client):
    """AppConfigAPI is registered on the client as app_config."""
    from pyfsr.api.app_config import AppConfigAPI

    assert hasattr(mock_client, "app_config")
    assert isinstance(mock_client.app_config, AppConfigAPI)
