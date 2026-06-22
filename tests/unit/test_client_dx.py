"""Unit tests for P6 client DX: timeout, retry, and auth-header masking."""

import requests

from pyfsr import FortiSOAR
from pyfsr.client import _mask_headers


# -- timeout ----------------------------------------------------------------
def test_default_timeout_applied(mock_client, mock_response, monkeypatch):
    captured = {}

    def cap(self, method, url, **kwargs):
        captured.update(kwargs)
        return mock_response()

    monkeypatch.setattr(requests.Session, "request", cap)
    mock_client.request("GET", "/api/3/alerts")
    assert captured["timeout"] == 30  # default


def test_explicit_timeout_overrides_default(mock_client, mock_response, monkeypatch):
    captured = {}

    def cap(self, method, url, **kwargs):
        captured.update(kwargs)
        return mock_response()

    monkeypatch.setattr(requests.Session, "request", cap)
    mock_client.request("GET", "/api/3/alerts", timeout=2)
    assert captured["timeout"] == 2


def test_configured_timeout(mock_client, mock_response, monkeypatch):
    # mock_client's session.request is already mocked, so building another
    # client (userpass auth) won't hit the network.
    client = FortiSOAR(
        base_url="https://t.example.com",
        username="u",
        password="p",
        verify_ssl=False,
        timeout=7,
    )
    captured = {}

    def cap(self, method, url, **kwargs):
        captured.update(kwargs)
        return mock_response()

    monkeypatch.setattr(requests.Session, "request", cap)
    client.request("GET", "/api/3/alerts")
    assert captured["timeout"] == 7


# -- retry ------------------------------------------------------------------
def test_retry_adapter_mounted_by_default(mock_client):
    adapter = mock_client.session.get_adapter("https://test.fortisoar.com")
    retry = adapter.max_retries
    assert retry.total == 2
    assert "GET" in retry.allowed_methods
    assert "POST" not in retry.allowed_methods  # writes never auto-retried
    assert 503 in retry.status_forcelist


def test_retry_disabled(mock_client):
    client = FortiSOAR(
        base_url="https://t.example.com",
        username="u",
        password="p",
        verify_ssl=False,
        max_retries=0,
    )
    # With retries off we don't mount a custom adapter; requests' default
    # adapter carries a no-retry policy.
    retry = client.session.get_adapter("https://t.example.com").max_retries
    assert getattr(retry, "total", 0) in (0, None) or retry == 0


# -- dry_run ----------------------------------------------------------------
def _dry_run_client(mock_response, monkeypatch, sent):
    """Build a dry_run client, capturing every method that reaches the session."""

    def cap(self, method, url, **kwargs):
        sent.append(method)
        if "/auth/authenticate" in url:
            return mock_response(json_data={"token": "tok"})
        return mock_response(json_data={"hydra:member": []})

    monkeypatch.setattr(requests.Session, "request", cap)
    client = FortiSOAR(
        base_url="https://t.example.com",
        username="u",
        password="p",
        verify_ssl=False,
        dry_run=True,
    )
    sent.clear()  # drop the auth call made during construction
    return client


def test_dry_run_suppresses_writes(mock_response, monkeypatch):
    sent = []
    client = _dry_run_client(mock_response, monkeypatch, sent)
    assert client.dry_run is True

    # Writes are suppressed and return a synthetic dry-run envelope.
    body = client.post("/api/3/alerts", data={"name": "x"})
    assert body["dryRun"] is True
    assert body["method"] == "POST"
    assert body["data"] == {"name": "x"}
    # delete returns None but still must not hit the network
    assert client.delete("/api/3/alerts/abc") is None
    assert sent == []  # nothing actually went out


def test_dry_run_passes_reads_through(mock_response, monkeypatch):
    sent = []
    client = _dry_run_client(mock_response, monkeypatch, sent)
    client.get("/api/3/alerts")
    assert sent == ["GET"]  # reads are not suppressed


def test_dry_run_defaults_off(mock_client):
    assert mock_client.dry_run is False


# -- auth-header masking ----------------------------------------------------
def test_mask_headers_keeps_scheme_hides_secret():
    masked = _mask_headers({"Authorization": "API-KEY supersecretvalue", "Content-Type": "application/json"})
    assert masked["Authorization"] == "API-KEY ***"
    assert "supersecretvalue" not in masked["Authorization"]
    assert masked["Content-Type"] == "application/json"  # untouched


def test_mask_headers_bare_secret():
    masked = _mask_headers({"X-Api-Key": "rawtoken", "Cookie": "session=abc"})
    assert masked["X-Api-Key"] == "***"  # no scheme prefix -> fully masked
    assert masked["Cookie"] == "***"
    assert "abc" not in masked["Cookie"]
