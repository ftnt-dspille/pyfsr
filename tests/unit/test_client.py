import pytest
import requests

from pyfsr.exceptions import (
    APIError,
    AuthenticationError,
    PermissionError,
    ResourceNotFoundError,
    ResponseParseError,
    ValidationError,
)


def test_request_success(mock_client, mock_response, monkeypatch):
    """Test successful request with JSON response"""
    expected_data = {"key": "value"}

    def mock_request(*args, **kwargs):
        return mock_response(json_data=expected_data)

    # Mock the session request
    monkeypatch.setattr(requests.Session, "request", mock_request)

    # Test GET request
    response = mock_client.request("GET", "/api/3/alerts")
    assert response.json() == expected_data


def test_request_binary_response(mock_client, mock_response, monkeypatch):
    """Test request returning binary data (like file downloads)"""
    binary_content = b"binary data"

    def mock_request(*args, **kwargs):
        response = mock_response()
        response.headers["Content-Type"] = "application/octet-stream"
        response._content = binary_content
        return response

    monkeypatch.setattr(requests.Session, "request", mock_request)

    response = mock_client.request("GET", "/api/export/file.zip")
    assert response.content == binary_content


def test_request_validation_error(mock_client, mock_response, monkeypatch):
    """Test handling of validation errors (400)"""
    error_response = {"type": "ValidationException", "message": "Invalid alert data"}

    def mock_request(*args, **kwargs):
        return mock_response(status_code=400, json_data=error_response)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(ValidationError) as exc:
        mock_client.request("POST", "/api/3/alerts", data={"invalid": "data"})
    assert "Invalid alert data" in str(exc.value)


def test_request_auth_error(mock_client, mock_response, monkeypatch):
    """Test handling of authentication errors (401)"""
    error_response = {"message": "Invalid API key"}

    def mock_request(*args, **kwargs):
        return mock_response(status_code=401, json_data=error_response)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(AuthenticationError) as exc:
        mock_client.request("GET", "/api/3/alerts")
    assert "Invalid API key" in str(exc.value)


def test_request_permission_error(mock_client, mock_response, monkeypatch):
    """Test handling of permission errors (403)"""
    error_response = {"message": "Insufficient permissions"}

    def mock_request(*args, **kwargs):
        return mock_response(status_code=403, json_data=error_response)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(PermissionError) as exc:
        mock_client.request("GET", "/api/3/alerts")
    assert "Insufficient permissions" in str(exc.value)


def test_request_not_found(mock_client, mock_response, monkeypatch):
    """Test handling of not found errors (404)"""
    error_response = {"message": "Alert not found"}

    def mock_request(*args, **kwargs):
        return mock_response(status_code=404, json_data=error_response)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(ResourceNotFoundError) as exc:
        mock_client.request("GET", "/api/3/alerts/non-existent")
    assert "Alert not found" in str(exc.value)


def test_request_server_error(mock_client, mock_response, monkeypatch):
    """Test handling of server errors (500)"""
    error_response = {"message": "Internal server error"}

    def mock_request(*args, **kwargs):
        return mock_response(status_code=500, json_data=error_response)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(APIError) as exc:
        mock_client.request("GET", "/api/3/alerts")
    assert "Internal server error" in str(exc.value)


def test_request_with_query_params(mock_client, mock_response, monkeypatch):
    """Test request with query parameters"""
    expected_params = {"status": "Open", "$limit": 10}

    def mock_request(*args, **kwargs):
        assert kwargs.get("params") == expected_params
        return mock_response(json_data={})

    monkeypatch.setattr(requests.Session, "request", mock_request)

    mock_client.request("GET", "/api/3/alerts", params=expected_params)


def test_request_with_files(mock_client, mock_response, monkeypatch):
    """Test request with file upload"""
    files = {"file": ("test.txt", b"content", "text/plain")}

    def mock_request(*args, **kwargs):
        assert "files" in kwargs
        assert kwargs["files"] == files
        return mock_response(json_data={"@type": "File", "filename": "test.txt"})

    monkeypatch.setattr(requests.Session, "request", mock_request)

    response = mock_client.request("POST", "/api/3/files", files=files)
    assert response.json()["@type"] == "File"


def test_request_with_custom_headers(mock_client, mock_response, monkeypatch):
    """Test request with custom headers"""
    custom_headers = {"X-Custom": "test"}

    def mock_request(*args, **kwargs):
        headers = kwargs.get("headers", {})
        assert "X-Custom" in headers
        assert headers["X-Custom"] == "test"
        return mock_response()

    monkeypatch.setattr(requests.Session, "request", mock_request)

    mock_client.request("GET", "/api/3/alerts", headers=custom_headers)


def test_request_network_error(mock_client, monkeypatch):
    """Test handling of network connection errors"""

    def mock_request(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Network error")

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(requests.exceptions.ConnectionError):
        mock_client.request("GET", "/api/3/alerts")


def test_request_timeout(mock_client, monkeypatch):
    """Test handling of request timeouts"""

    def mock_request(*args, **kwargs):
        raise requests.exceptions.Timeout("Request timed out")

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(requests.exceptions.Timeout):
        mock_client.request("GET", "/api/3/alerts")


def test_request_json_decode_error(mock_client, mock_response, monkeypatch):
    """Test handling of invalid JSON responses"""

    def mock_request(*args, **kwargs):
        response = mock_response()
        response._content = b"Invalid JSON"
        return response

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(ResponseParseError) as exc_info:
        mock_client.get("/api/3/alerts")
    assert "not valid JSON" in str(exc_info.value)
    assert exc_info.value.status_code == 200


def test_request_exception_logging(mock_client, mock_response, monkeypatch):
    """Test logging when RequestException is raised both with and without response"""

    # Case 1: RequestException with response
    error_response = mock_response(status_code=500, json_data={"message": "Server Error"})
    error_with_response = requests.exceptions.RequestException("Test error")
    error_with_response.response = error_response

    def mock_request_with_response(*args, **kwargs):
        raise error_with_response

    monkeypatch.setattr(requests.Session, "request", mock_request_with_response)

    # Enable verbose mode for logging
    mock_client.verbose = True


def test_request_reauths_and_retries_on_expired_token(mock_client, mock_response, monkeypatch):
    """A 401/403 on a token-auth client triggers one re-auth + replay (recovers
    from an expired session token mid-run instead of failing the request)."""
    import requests as _rq

    state = {"auth_calls": 0, "data_calls": 0}

    def mock_request(self, method, url, **kwargs):
        if "/auth/authenticate" in url:
            state["auth_calls"] += 1
            return mock_response(json_data={"token": f"tok-{state['auth_calls']}"})
        state["data_calls"] += 1
        if state["data_calls"] == 1:
            return mock_response(status_code=403, json_data={"message": "HMAC signature has expired"})
        return mock_response(json_data={"ok": True})

    monkeypatch.setattr(_rq.sessions.Session, "request", mock_request)
    resp = mock_client.request("GET", "/api/3/alerts")
    assert resp.json() == {"ok": True}
    assert state["data_calls"] == 2  # original + one replay
    assert state["auth_calls"] >= 1  # refreshed at least once


def test_request_reauth_failure_is_logged_not_swallowed(mock_client, mock_response, monkeypatch, caplog):
    """If auth.refresh() itself raises (network error, rotated creds, bug), the
    failure must be logged, not silently discarded — otherwise only the
    original 401/403 is ever visible and the real cause is invisible."""
    import logging as _logging

    import requests as _rq

    def mock_request(self, method, url, **kwargs):
        return mock_response(status_code=403, json_data={"message": "HMAC signature has expired"})

    monkeypatch.setattr(_rq.sessions.Session, "request", mock_request)

    def broken_refresh():
        raise RuntimeError("refresh backend unreachable")

    monkeypatch.setattr(mock_client.auth, "refresh", broken_refresh)

    with caplog.at_level(_logging.WARNING, logger="pyfsr"):
        mock_client.request("GET", "/api/3/alerts", raise_on_status=False)

    assert any("refresh backend unreachable" in r.message for r in caplog.records)


def test_request_reauth_fires_only_once(mock_client, mock_response, monkeypatch):
    """If the replay still 401/403s, the client gives up (no infinite loop)."""
    import requests as _rq

    state = {"data_calls": 0}

    def mock_request(self, method, url, **kwargs):
        if "/auth/authenticate" in url:
            return mock_response(json_data={"token": "tok"})
        state["data_calls"] += 1
        return mock_response(status_code=403, json_data={"message": "still expired"})

    monkeypatch.setattr(_rq.sessions.Session, "request", mock_request)
    with pytest.raises(PermissionError):
        mock_client.request("GET", "/api/3/alerts")
    assert state["data_calls"] == 2  # original + exactly one replay, then raise


# -- raise_on_status (fire-and-observe-status probes) -----------------------
def test_request_raise_on_status_false_returns_raw_response(mock_client, mock_response, monkeypatch):
    """raise_on_status=False returns the raw Response on a 4xx instead of raising."""
    error = {"message": "Not found"}

    def mock_request(*args, **kwargs):
        return mock_response(status_code=404, json_data=error)

    monkeypatch.setattr(requests.Session, "request", mock_request)
    resp = mock_client.request("GET", "/api/3/alerts/missing", raise_on_status=False)
    assert resp.status_code == 404
    assert resp.json() == error


def test_get_raise_on_status_false_returns_response_not_json(mock_client, mock_response, monkeypatch):
    """client.get(raise_on_status=False) returns the raw Response (not parsed JSON)."""
    error = {"message": "Forbidden"}

    def mock_request(*args, **kwargs):
        return mock_response(status_code=403, json_data=error)

    monkeypatch.setattr(requests.Session, "request", mock_request)
    resp = mock_client.get("/api/3/alerts", raise_on_status=False)
    assert isinstance(resp, requests.Response)
    assert resp.status_code == 403
    assert resp.json() == error


def test_post_and_delete_raise_on_status_false_return_response(mock_client, mock_response, monkeypatch):
    def mock_request(*args, **kwargs):
        return mock_response(status_code=404, json_data={"message": "nope"})

    monkeypatch.setattr(requests.Session, "request", mock_request)
    post_resp = mock_client.post("/api/3/alerts", data={}, raise_on_status=False)
    assert isinstance(post_resp, requests.Response) and post_resp.status_code == 404
    del_resp = mock_client.delete("/api/3/alerts/x", raise_on_status=False)
    assert isinstance(del_resp, requests.Response) and del_resp.status_code == 404


def test_raise_on_status_default_still_raises(mock_client, mock_response, monkeypatch):
    """The default (raise_on_status=True) is unchanged — 4xx still raises."""

    def mock_request(*args, **kwargs):
        return mock_response(status_code=404, json_data={"message": "Not found"})

    monkeypatch.setattr(requests.Session, "request", mock_request)
    with pytest.raises(ResourceNotFoundError):
        mock_client.get("/api/3/alerts/missing")


def test_raise_on_status_false_still_raises_on_network_error(mock_client, monkeypatch):
    """raise_on_status=False suppresses status errors but NOT transport errors."""

    def mock_request(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Network error")

    monkeypatch.setattr(requests.Session, "request", mock_request)
    with pytest.raises(requests.exceptions.ConnectionError):
        mock_client.get("/api/3/alerts", raise_on_status=False)


def test_raise_on_status_false_preserved_through_reauth(mock_client, mock_response, monkeypatch):
    """The flag survives the 401→reauth→replay path: a refreshed 200 is returned
    as a raw Response (not parsed), and the original 401 didn't raise."""
    import requests as _rq

    state = {"data_calls": 0}

    def mock_request(self, method, url, **kwargs):
        if "/auth/authenticate" in url:
            return mock_response(json_data={"token": "tok-2"})
        state["data_calls"] += 1
        if state["data_calls"] == 1:
            return mock_response(status_code=401, json_data={"message": "expired"})
        return mock_response(json_data={"ok": True})

    monkeypatch.setattr(_rq.sessions.Session, "request", mock_request)
    resp = mock_client.get("/api/3/alerts", raise_on_status=False)
    assert isinstance(resp, requests.Response)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert state["data_calls"] == 2  # original 401 + one replay after refresh


# -- version() fallback chain ------------------------------------------------
def test_version_from_cyops_version_json(mock_client, mock_response, monkeypatch):
    """version() prefers /cyops_version.json on the configured base port."""

    def mock_request(*args, **kwargs):
        url = " ".join(str(a) for a in args) + " " + " ".join(str(v) for v in kwargs.values())
        if "/cyops_version.json" in url:
            return mock_response(json_data={"version": "7.6.5-5662"})
        return mock_response(status_code=404)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    assert mock_client.version() == "7.6.5-5662"


def test_version_from_appliances_endpoint(mock_client, mock_response, monkeypatch):
    """version() returns version string from /api/3/appliances."""

    def mock_request(*args, **kwargs):
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
        if "/api/3/appliances" in url:
            return mock_response(json_data={"@version": "7.4.2", "build": "123"})
        return mock_response(status_code=404)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    version = mock_client.version()
    assert version == "7.4.2"


def test_version_fallback_to_license_endpoint(mock_client, mock_response, monkeypatch):
    """version() falls back to /api/auth/license when /api/3/appliances fails."""

    def mock_request(*args, **kwargs):
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
        if "/api/3/appliances" in url:
            return mock_response(status_code=404, json_data={"message": "Not found"})
        if "/api/auth/license" in url:
            return mock_response(json_data={"version": "7.3.1"})
        return mock_response(status_code=404)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    version = mock_client.version()
    assert version == "7.3.1"


def test_version_fallback_to_system_version_endpoint(mock_client, mock_response, monkeypatch):
    """version() falls back to /api/version when appliances and license fail."""

    def mock_request(*args, **kwargs):
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
        if "/api/3/appliances" in url:
            return mock_response(status_code=404, json_data={"message": "Not found"})
        if "/api/auth/license" in url:
            return mock_response(status_code=404, json_data={"message": "Not found"})
        if "/api/version" in url:
            return mock_response(json_data={"version": "7.2.0"})
        return mock_response(status_code=404)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    version = mock_client.version()
    assert version == "7.2.0"


def test_version_returns_dict_when_multiple_fields(mock_client, mock_response, monkeypatch):
    """version() returns dict with version + build when both are present."""

    def mock_request(*args, **kwargs):
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
        if "/api/3/appliances" in url:
            return mock_response(
                json_data={
                    "@version": "7.4.2",
                    "build": "456",
                    "@id": "/appliances/1",
                }
            )
        return mock_response(status_code=404)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    version = mock_client.version()
    # Should return the first non-@type, non-special key or the version string
    assert version == "7.4.2"


def test_version_raises_when_all_endpoints_fail(mock_client, mock_response, monkeypatch):
    """version() raises FortiSOARException when all fallback endpoints fail."""
    from pyfsr.exceptions import FortiSOARException

    def mock_request(*args, **kwargs):
        return mock_response(status_code=404, json_data={"message": "Not found"})

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(FortiSOARException) as exc:
        mock_client.version()

    error_msg = str(exc.value)
    assert "Could not retrieve FortiSOAR version" in error_msg
    assert "/api/3/appliances" in error_msg
    assert "/api/auth/license" in error_msg
    assert "/api/version" in error_msg


def test_version_returns_appliances_dict_with_extra_fields(mock_client, mock_response, monkeypatch):
    """version() returns full dict from /api/3/appliances when fields present."""

    def mock_request(*args, **kwargs):
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
        if "/api/3/appliances" in url:
            return mock_response(
                json_data={
                    "@version": "7.5.0",
                    "build": "789",
                    "name": "FortiSOAR",
                    "@id": "/appliances/1",
                }
            )
        return mock_response(status_code=404)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    version = mock_client.version()
    # Should return the @version string since it exists
    assert version == "7.5.0"


def test_version_returns_license_dict(mock_client, mock_response, monkeypatch):
    """version() returns dict from license endpoint if appliances fails."""

    def mock_request(*args, **kwargs):
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
        if "/api/3/appliances" in url:
            return mock_response(status_code=404, json_data={"message": "Not found"})
        if "/api/auth/license" in url:
            return mock_response(
                json_data={
                    "version": "7.3.1",
                    "licensee": "Test Corp",
                    "expiryDate": "2025-12-31",
                }
            )
        return mock_response(status_code=404)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    version = mock_client.version()
    assert version == "7.3.1"


def test_version_with_network_error_falls_back(mock_client, mock_response, monkeypatch):
    """version() tolerates exceptions and tries next endpoint."""

    def mock_request(*args, **kwargs):
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
        if "/api/3/appliances" in url:
            raise requests.exceptions.ConnectionError("Network error")
        if "/api/auth/license" in url:
            return mock_response(json_data={"version": "7.3.0"})
        return mock_response(status_code=404)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    version = mock_client.version()
    assert version == "7.3.0"


def test_version_exhausts_all_fallbacks_then_raises(mock_client, mock_response, monkeypatch):
    """version() tries all 3 endpoints before raising exception."""
    from pyfsr.exceptions import FortiSOARException

    call_count = {"appliances": 0, "license": 0, "version": 0}

    def mock_request(*args, **kwargs):
        url = args[1] if len(args) > 1 else kwargs.get("url", "")
        if "/api/3/appliances" in url:
            call_count["appliances"] += 1
            return mock_response(status_code=404)
        if "/api/auth/license" in url:
            call_count["license"] += 1
            return mock_response(status_code=404)
        if "/api/version" in url:
            call_count["version"] += 1
            return mock_response(status_code=404)
        return mock_response(status_code=404)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(FortiSOARException):
        mock_client.version()

    # All three endpoints should have been attempted
    assert call_count["appliances"] >= 1
    assert call_count["license"] >= 1
    assert call_count["version"] >= 1


def test_retry_backoff_and_status_forcelist_are_configurable(mock_client):
    """retry_backoff_factor/retry_status_forcelist reach the mounted Retry adapter,
    so a caller doesn't have to hand-roll their own HTTPAdapter to tune backoff
    for a chatty polling loop or a box known to need a longer recovery window."""
    from pyfsr import FortiSOAR

    client = FortiSOAR(
        base_url="https://test.fortisoar.com",
        token="test-key",
        retry_backoff_factor=2.5,
        retry_status_forcelist=(429, 503),
    )
    adapter = client.session.get_adapter("https://test.fortisoar.com")
    retry = adapter.max_retries
    assert retry.backoff_factor == 2.5
    assert retry.status_forcelist == (429, 503)


def test_retry_defaults_unchanged(mock_client):
    """Default construction keeps the existing backoff/status-forcelist behavior."""
    retry = mock_client.session.get_adapter("https://test.fortisoar.com").max_retries
    assert retry.backoff_factor == 0.5
    assert retry.status_forcelist == (429, 500, 502, 503, 504)
