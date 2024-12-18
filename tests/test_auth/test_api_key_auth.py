import pytest
from pyfsr.auth.api_key import APIKeyAuth


def test_api_key_auth_headers():
    """Test API key authentication headers"""
    api_key = "test-key-12345"
    auth = APIKeyAuth(api_key)
    headers = auth.get_auth_headers()

    assert headers['Authorization'] == f'API-KEY {api_key}'
    assert headers['Content-Type'] == 'application/json'


def test_api_key_auth_invalid_key():
    """Test API key validation"""
    with pytest.raises(ValueError):
        APIKeyAuth("")
