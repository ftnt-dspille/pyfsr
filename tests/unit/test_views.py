"""Tests for the system view template (SVT) resolution API."""

import pytest


def _capture(monkeypatch, mock_response, json_data):
    captured = {}

    def mock_request(*args, **kwargs):
        captured.update(kwargs)
        return mock_response(json_data=json_data)

    monkeypatch.setattr("requests.Session.request", mock_request)
    return captured


_SVT = {
    "uuid": "817eeca5-52db-4198-b655-ebce665b361f",
    "type": "rows",
    "name": "def",
    "module": "alerts",
    "isDefault": True,
    "config": {"tabs": []},
}


def test_detail_resolves_active_svt(mock_client, mock_response, monkeypatch):
    captured = _capture(monkeypatch, mock_response, _SVT)
    svt = mock_client.views.detail("alerts")
    assert captured["url"].endswith("/api/views/1/modules-alerts-detail")
    assert svt["uuid"] == _SVT["uuid"]
    assert svt["type"] == "rows"


def test_listing_and_form_paths(mock_client, mock_response, monkeypatch):
    captured = _capture(monkeypatch, mock_response, _SVT)
    mock_client.views.listing("incidents")
    assert captured["url"].endswith("/api/views/1/modules-incidents-list")
    mock_client.views.form("tasks")
    assert captured["url"].endswith("/api/views/1/modules-tasks-form")


def test_resolve_generic(mock_client, mock_response, monkeypatch):
    captured = _capture(monkeypatch, mock_response, _SVT)
    mock_client.views.resolve("alerts", "detail")
    assert captured["url"].endswith("/api/views/1/modules-alerts-detail")


def test_resolve_rejects_bad_kind(mock_client, mock_response, monkeypatch):
    _capture(monkeypatch, mock_response, _SVT)
    with pytest.raises(ValueError, match="kind must be one of"):
        mock_client.views.resolve("alerts", "grid")
