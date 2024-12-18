from unittest.mock import Mock

import pytest

from pyfsr import FortiSOAR


@pytest.fixture
def mock_response():
    """Create a mock response with customizable attributes"""

    def _mock_response(status_code=200, json_data=None, raise_error=False):
        mock = Mock()
        mock.status_code = status_code
        mock.json.return_value = json_data or {}

        if raise_error:
            mock.raise_for_status.side_effect = Exception("Mocked error")
        return mock

    return _mock_response


@pytest.fixture
def base_url():
    """Base URL for testing"""
    return "https://test.fortisoar.com"


@pytest.fixture
def api_key():
    """Sample API key for testing"""
    return "test-api-key-12345"


@pytest.fixture
def mock_client(base_url, api_key, mocker):
    """Create a mocked FortiSOAR client"""
    client = FortiSOAR(base_url, auth=api_key)
    mocker.patch.object(client.session, 'request')
    return client
