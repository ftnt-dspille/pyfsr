import pytest
import requests

from pyfsr.auth.api_key import APIKeyAuth
from pyfsr.auth.base import BaseAuth
from pyfsr.exceptions import APIError, UnsupportedAuthOperationError


def test_api_key_initialization_success(mocker):
    """Test successful API key initialization"""
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "success"}

    auth = APIKeyAuth(
        base_url="https://test.fortisoar.com",
        api_key="test-key-123"
    )

    assert auth.api_key == "test-key-123"
    assert auth.base_url == "https://test.fortisoar.com"
    assert auth.verify_ssl is True


def test_api_key_strips_trailing_slash(mocker):
    """Test base URL trailing slash is stripped"""
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "success"}

    auth = APIKeyAuth(
        base_url="https://test.fortisoar.com/",
        api_key="test-key-123"
    )

    assert auth.base_url == "https://test.fortisoar.com"


def test_api_key_headers(mocker):
    """Test API key authentication headers are correctly formatted"""
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "success"}

    auth = APIKeyAuth(
        base_url="https://test.fortisoar.com",
        api_key="test-key-123"
    )

    headers = auth.get_auth_headers()
    assert headers == {
        'Authorization': 'API-KEY test-key-123',
        'Content-Type': 'application/json'
    }


def test_api_key_validation_failed_auth(mocker):
    """Test API key validation with failed authentication"""
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.status_code = 401
    mock_get.return_value.json.return_value = {"error": "Invalid authentication"}

    with pytest.raises(APIError) as exc_info:
        APIKeyAuth(
            base_url="https://test.fortisoar.com",
            api_key="invalid-key"
        )

    assert "Invalid API key - authentication failed" in str(exc_info.value)


def test_api_key_validation_server_error(mocker):
    """Test API key validation with server error"""
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.status_code = 500
    mock_get.return_value.json.return_value = {"error": "Internal server error"}
    mock_get.return_value.text = "Internal server error"

    with pytest.raises(APIError) as exc_info:
        APIKeyAuth(
            base_url="https://test.fortisoar.com",
            api_key="test-key-123"
        )

    assert "API key validation failed with status 500" in str(exc_info.value)


def test_api_key_validation_connection_error(mocker):
    """Test API key validation with connection error"""
    mock_get = mocker.patch('requests.get')
    mock_get.side_effect = requests.exceptions.ConnectionError("Connection failed")

    with pytest.raises(APIError) as exc_info:
        APIKeyAuth(
            base_url="https://test.fortisoar.com",
            api_key="test-key-123"
        )

    assert "API key validation request failed" in str(exc_info.value)


def test_api_key_ssl_verification(mocker):
    """Test SSL verification settings are respected"""
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "success"}

    auth = APIKeyAuth(
        base_url="https://test.fortisoar.com",
        api_key="test-key-123",
        verify_ssl=False
    )

    assert auth.verify_ssl is False
    # Verify the request was made with verify=False
    mock_get.assert_called_with(
        "https://test.fortisoar.com/api/3/people",
        headers=mocker.ANY,
        verify=False
    )


def test_api_key_unsupported_operations(mocker):
    """Test unsupported operations are properly restricted"""
    mock_get = mocker.patch('requests.get')
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "success"}

    auth = APIKeyAuth(
        base_url="https://test.fortisoar.com",
        api_key="test-key-123"
    )

    # Check that auth operations are blocked
    with pytest.raises(UnsupportedAuthOperationError) as exc_info:
        auth.check_operation_supported(BaseAuth.OPERATION_AUTH)
    assert "Operation 'auth' is not supported" in str(exc_info.value)

    # Check that config export is blocked
    with pytest.raises(UnsupportedAuthOperationError) as exc_info:
        auth.check_operation_supported(BaseAuth.OPERATION_CONFIG_EXPORT)
    assert "Operation 'config_export' is not supported" in str(exc_info.value)

    # Check that other operations are allowed
    auth.check_operation_supported(BaseAuth.OPERATION_PLAYBOOK)
    auth.check_operation_supported(BaseAuth.OPERATION_SOLUTION_PACK)


def test_api_key_is_valid_method(mocker):
    """Test is_valid() method for checking API key validity"""
    mock_get = mocker.patch('requests.get')

    # First make key valid during initialization
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "success"}

    auth = APIKeyAuth(
        base_url="https://test.fortisoar.com",
        api_key="test-key-123"
    )

    # Test valid key
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"status": "success"}
    assert auth.is_valid() is True

    # Test invalid key
    mock_get.return_value.status_code = 401
    mock_get.return_value.json.return_value = {"error": "Invalid authentication"}
    assert auth.is_valid() is False
