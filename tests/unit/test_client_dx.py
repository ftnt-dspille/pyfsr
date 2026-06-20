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
