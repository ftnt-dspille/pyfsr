"""Tests for the typed CRUD shortcuts: comments, tasks, incidents."""

import pytest


def _capture(monkeypatch, mock_response, json_data=None):
    """Patch Session.request and capture the outgoing call kwargs."""
    captured = {}

    def mock_request(*args, **kwargs):
        captured.update(kwargs)
        return mock_response(json_data=json_data if json_data is not None else {"@type": "Comment"})

    monkeypatch.setattr("requests.Session.request", mock_request)
    return captured


def test_comment_create_links_record(mock_client, mock_response, monkeypatch):
    captured = _capture(monkeypatch, mock_response)
    mock_client.comments.create("Triaged — false positive.", record="/api/3/alerts/abc-123")

    assert captured["url"].endswith("/api/3/comments")
    body = captured["json"]
    assert body["content"] == "Triaged — false positive."
    # relationship field derived from the IRI module segment
    assert body["alerts"] == ["/api/3/alerts/abc-123"]


def test_comment_create_no_record(mock_client, mock_response, monkeypatch):
    captured = _capture(monkeypatch, mock_response)
    mock_client.comments.create("standalone note")
    assert captured["json"] == {"content": "standalone note"}


def test_comment_create_rejects_mixed_modules(mock_client, mock_response, monkeypatch):
    _capture(monkeypatch, mock_response)
    with pytest.raises(ValueError, match="share one module"):
        mock_client.comments.create("x", record=["/api/3/alerts/a", "/api/3/incidents/b"])


def test_task_create_links_alert(mock_client, mock_response, monkeypatch):
    captured = _capture(monkeypatch, mock_response, json_data={"@type": "Task"})
    # resolve_picklists=False avoids the metadata lookup in a unit test
    mock_client.tasks.create(
        name="Check IP reputation",
        record="/api/3/alerts/abc-123",
        resolve_picklists=False,
    )
    body = captured["json"]
    assert captured["url"].endswith("/api/3/tasks")
    assert body["name"] == "Check IP reputation"
    assert body["alerts"] == ["/api/3/alerts/abc-123"]


def test_incident_create_links_alert(mock_client, mock_response, monkeypatch):
    captured = _capture(monkeypatch, mock_response, json_data={"@type": "Incident"})
    mock_client.incidents.create(
        name="INC — Suspicious Login",
        record="/api/3/alerts/abc-123",
        resolve_picklists=False,
    )
    body = captured["json"]
    assert captured["url"].endswith("/api/3/incidents")
    assert body["alerts"] == ["/api/3/alerts/abc-123"]


def test_record_module_get_delete(mock_client, mock_response, monkeypatch):
    captured = _capture(monkeypatch, mock_response, json_data={"@type": "Task"})
    mock_client.tasks.get("task-1")
    assert captured["url"].endswith("/api/3/tasks/task-1")
    mock_client.tasks.delete("task-1")
    assert captured["method"] == "DELETE"
