import pytest

from pyfsr import FortiSOAR
from pyfsr.exceptions import FortiSOARException


def test_client_initialization_with_api_key():
    """Test client initialization with API key"""
    client = FortiSOAR("https://test.com", auth="test-api-key")
    assert client.base_url == "https://test.com"
    assert "API-KEY" in client.session.headers['Authorization']


def test_client_initialization_with_user_pass(mocker, mock_response):
    """Test client initialization with username/password"""
    mock_token = "mock-jwt-token"
    mocker.patch('requests.post',
                 return_value=mock_response(json_data={'token': mock_token}))

    client = FortiSOAR("https://test.com", auth=("username", "password"))
    assert client.base_url == "https://test.com"
    assert f"Bearer {mock_token}" in client.session.headers['Authorization']


def test_client_invalid_auth():
    """Test client initialization with invalid auth"""
    with pytest.raises(ValueError):
        FortiSOAR("https://test.com", auth=None)


def test_client_request_error(mock_client, mock_response):
    """Test client request error handling"""
    mock_client.session.request.return_value = mock_response(
        status_code=500,
        raise_error=True
    )

    with pytest.raises(FortiSOARException):
        mock_client.request('GET', '/test')
