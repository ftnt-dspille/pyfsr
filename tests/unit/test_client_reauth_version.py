"""Client tests for the token-refresh replay path and the version() fallback chain."""

import pytest
import requests
from requests.sessions import Session

from pyfsr.exceptions import FortiSOARException


def test_reauth_replays_request_once_on_401(mock_client, mock_response, monkeypatch):
    """A 401 with refreshable auth triggers one refresh + one replay, then succeeds."""
    calls = {"n": 0}

    def flaky(self, method, url, **kwargs):
        if "/auth/authenticate" in url:
            return mock_response(json_data={"token": "mock-fresh-token"})
        calls["n"] += 1
        if calls["n"] == 1:
            return mock_response(status_code=401, json_data={"message": "HMAC signature has expired"})
        return mock_response(json_data={"ok": True})

    monkeypatch.setattr(Session, "request", flaky)

    result = mock_client.request("GET", "/api/3/alerts")
    assert result.status_code == 200
    assert calls["n"] == 2  # original + replay


def test_reauth_fires_at_most_once(mock_client, mock_response, monkeypatch):
    """A persistent 401 after refresh surfaces the error, not an infinite replay loop."""
    calls = {"n": 0}

    def always_401(self, method, url, **kwargs):
        if "/auth/authenticate" in url:
            return mock_response(json_data={"token": "mock-fresh-token"})
        calls["n"] += 1
        return mock_response(status_code=401, json_data={"message": "still expired"})

    monkeypatch.setattr(Session, "request", always_401)

    with pytest.raises(FortiSOARException):
        mock_client.request("GET", "/api/3/alerts")
    assert calls["n"] == 2  # original + exactly one replay


def test_reauth_skipped_for_file_uploads(mock_client, mock_response, monkeypatch):
    """File uploads aren't replayed (stream already consumed); the 401 surfaces directly."""
    calls = {"n": 0}

    def upload_401(self, method, url, **kwargs):
        if "/auth/authenticate" in url:
            return mock_response(json_data={"token": "mock-fresh-token"})
        calls["n"] += 1
        return mock_response(status_code=401, json_data={"message": "expired"})

    monkeypatch.setattr(Session, "request", upload_401)

    with pytest.raises(FortiSOARException):
        mock_client.request("POST", "/api/3/files", files={"file": ("x", b"data", "text/plain")})
    assert calls["n"] == 1  # no replay


def test_reauth_survives_refresh_failure(mock_client, mock_response, monkeypatch):
    """If refresh() itself raises, the original 401 still surfaces (logged, not swallowed)."""

    def failing_refresh():
        raise requests.exceptions.ConnectionError("network down")

    monkeypatch.setattr(mock_client.auth, "refresh", failing_refresh)

    def resp_401(self, method, url, **kwargs):
        return mock_response(status_code=401, json_data={"message": "expired"})

    monkeypatch.setattr(Session, "request", resp_401)

    with pytest.raises(FortiSOARException):
        mock_client.request("GET", "/api/3/alerts")


# -- version() fallback chain ------------------------------------------------
def test_version_primary_cyops_json(mock_client, mock_response, monkeypatch):
    def session_get(self, url, **kwargs):
        return mock_response(json_data={"version": "8.0.0-6034"})

    monkeypatch.setattr(Session, "get", session_get)
    assert mock_client.version(refresh=True) == "8.0.0-6034"


def test_version_falls_back_to_appliances(mock_client, mock_response, monkeypatch):
    def session_get(self, url, **kwargs):
        return mock_response(status_code=404, json_data={})

    monkeypatch.setattr(Session, "get", session_get)
    monkeypatch.setattr(mock_client, "get", lambda ep, **kw: {"@version": "7.6.5"})

    assert mock_client.version(refresh=True) == "7.6.5"


def test_version_all_fallbacks_exhausted_raises(mock_client, mock_response, monkeypatch):
    def session_get(self, url, **kwargs):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(Session, "get", session_get)

    def boom(*a, **k):
        raise FortiSOARException("no")

    monkeypatch.setattr(mock_client, "get", boom)
    monkeypatch.setattr(mock_client.system, "license", boom)
    monkeypatch.setattr(mock_client.system, "version", boom)

    with pytest.raises(FortiSOARException, match="Could not retrieve FortiSOAR version"):
        mock_client.version(refresh=True)


def test_version_is_cached(mock_client, mock_response, monkeypatch):
    calls = {"n": 0}

    def session_get(self, url, **kwargs):
        calls["n"] += 1
        return mock_response(json_data={"version": "8.0.0"})

    monkeypatch.setattr(Session, "get", session_get)
    assert mock_client.version(refresh=True) == "8.0.0"
    assert mock_client.version() == "8.0.0"
    assert calls["n"] == 1  # second call served from cache


def test_version_tuple_parses_build_qualified_string(mock_client, monkeypatch):
    monkeypatch.setattr(mock_client, "version", lambda refresh=False: "8.0.0-6034")
    assert mock_client.version_tuple(refresh=True) == (8, 0, 0)


def test_version_tuple_none_on_version_failure(mock_client, monkeypatch):
    def boom(refresh=False):
        raise FortiSOARException("all endpoints failed")

    monkeypatch.setattr(mock_client, "version", boom)
    assert mock_client.version_tuple() is None
