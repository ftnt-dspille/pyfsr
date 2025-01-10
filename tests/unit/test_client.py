import pytest
import requests

from pyfsr.exceptions import (
    ValidationError,
    AuthenticationError,
    ResourceNotFoundError,
    PermissionError,
    APIError
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
    error_response = {
        "type": "ValidationException",
        "message": "Invalid alert data"
    }

    def mock_request(*args, **kwargs):
        return mock_response(status_code=400, json_data=error_response)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(ValidationError) as exc:
        mock_client.request("POST", "/api/3/alerts", data={"invalid": "data"})
    assert "Invalid alert data" in str(exc.value)


def test_request_auth_error(mock_client, mock_response, monkeypatch):
    """Test handling of authentication errors (401)"""
    error_response = {
        "message": "Invalid API key"
    }

    def mock_request(*args, **kwargs):
        return mock_response(status_code=401, json_data=error_response)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(AuthenticationError) as exc:
        mock_client.request("GET", "/api/3/alerts")
    assert "Invalid API key" in str(exc.value)


def test_request_permission_error(mock_client, mock_response, monkeypatch):
    """Test handling of permission errors (403)"""
    error_response = {
        "message": "Insufficient permissions"
    }

    def mock_request(*args, **kwargs):
        return mock_response(status_code=403, json_data=error_response)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(PermissionError) as exc:
        mock_client.request("GET", "/api/3/alerts")
    assert "Insufficient permissions" in str(exc.value)


def test_request_not_found(mock_client, mock_response, monkeypatch):
    """Test handling of not found errors (404)"""
    error_response = {
        "message": "Alert not found"
    }

    def mock_request(*args, **kwargs):
        return mock_response(status_code=404, json_data=error_response)

    monkeypatch.setattr(requests.Session, "request", mock_request)

    with pytest.raises(ResourceNotFoundError) as exc:
        mock_client.request("GET", "/api/3/alerts/non-existent")
    assert "Alert not found" in str(exc.value)


def test_request_server_error(mock_client, mock_response, monkeypatch):
    """Test handling of server errors (500)"""
    error_response = {
        "message": "Internal server error"
    }

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

    with pytest.raises(requests.exceptions.JSONDecodeError):
        mock_client.get("/api/3/alerts")


def test_request_exception_logging(mock_client, mock_response, monkeypatch):
    """Test logging when RequestException is raised both with and without response"""

    # Case 1: RequestException with response
    error_response = mock_response(
        status_code=500,
        json_data={"message": "Server Error"}
    )
    error_with_response = requests.exceptions.RequestException("Test error")
    error_with_response.response = error_response

    def mock_request_with_response(*args, **kwargs):
        raise error_with_response

    monkeypatch.setattr(requests.Session, "request", mock_request_with_response)

    # Enable verbose mode for logging
    mock_client.verbose = True