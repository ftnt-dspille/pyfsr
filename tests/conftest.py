import pytest

from pyfsr import FortiSOAR


@pytest.fixture
def mock_auth_response(mock_response):
    """Mock successful auth response"""
    return mock_response(json_data={
        "token": "mock-jwt-token-123"  # Match FortiSOAR response format
    })


@pytest.fixture
def mock_response():
    """Create a mock response with custom status code and data."""

    def _mock_response(status_code=200, json_data=None, raise_error=None):
        response = Response()
        response.status_code = status_code

        # Allow passing a dict that will be returned as json
        if json_data is not None:
            response._content = json.dumps(json_data).encode('utf-8')
            response.json = lambda: json_data

        # Set up raise_for_status behavior
        def raise_for_status():
            if status_code >= 400:
                raise requests.exceptions.HTTPError(
                    f"HTTP Error {status_code}",
                    response=response
                )

        response.raise_for_status = raise_for_status
        return response

    return _mock_response


@pytest.fixture
def mock_client():
    """Create a FortiSOAR client instance for testing."""
    from pyfsr import FortiSOAR
    client = FortiSOAR(
        base_url="https://test.fortisoar.com",
        auth=("test_user", "test_pass"),
        verify_ssl=False,
        suppress_insecure_warnings=True
    )
    # Pre-set the token to avoid auth requests in every test
    client.auth.token = "mock-jwt-token-123"
    return client


@pytest.fixture
def mock_responses():
    """Load mock response data from JSON files."""

    def load_mock_response(filename):
        path = Path(__file__).parent / 'resources' / 'mock_responses' / filename
        with open(path) as f:
            return json.load(f)

    return load_mock_response


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring integration with real FortiSOAR instance"
    )


# tests/test_auth.py
def test_user_password_auth(mock_client):
    """Test authentication with username and password"""
    assert mock_client.auth.get_auth_headers()["Authorization"].startswith("Bearer ")


def test_api_key_auth():
    """Test authentication with API key"""
    client = FortiSOAR("https://test.fortisoar.com", "test-api-key")
    assert client.auth.get_auth_headers()["Authorization"].startswith("API-KEY ")


# tests/test_alerts.py
def test_create_alert(mock_client, mock_response, monkeypatch):
    """Test creating an alert"""
    expected_response = {
        "@id": "/api/3/alerts/123",
        "@type": "Alert",
        "name": "Test Alert",
        "severity": "/api/3/picklists/456"
    }

    # Mock the post request to return our expected data
    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=expected_response)
    )

    alert_data = {
        "name": "Test Alert",
        "severity": "/api/3/picklists/456"
    }

    result = mock_client.alerts.create(**alert_data)
    assert result["@type"] == "Alert"
    assert result["name"] == "Test Alert"


def test_get_alert(mock_client, mock_response, monkeypatch):
    """Test retrieving a specific alert"""
    alert_id = "123"
    expected_response = {
        "@id": f"/api/3/alerts/{alert_id}",
        "@type": "Alert",
        "name": "Test Alert"
    }

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=expected_response)
    )

    result = mock_client.alerts.get(alert_id)
    assert result["@id"] == f"/api/3/alerts/{alert_id}"


def test_list_alerts(mock_client, mock_response, monkeypatch):
    """Test listing alerts"""
    expected_response = {
        "@context": "/api/3/contexts/Alert",
        "@id": "/api/3/alerts",
        "@type": "hydra:PagedCollection",
        "hydra:member": [
            {
                "@id": "/api/3/alerts/123",
                "@type": "Alert",
                "name": "Test Alert 1"
            },
            {
                "@id": "/api/3/alerts/456",
                "@type": "Alert",
                "name": "Test Alert 2"
            }
        ],
        "hydra:totalItems": 2
    }

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=expected_response)
    )

    result = mock_client.alerts.list()
    assert len(result["hydra:member"]) == 2
    assert result["hydra:totalItems"] == 2


# tests/test_solution_packs.py
def test_find_installed_pack(mock_client, mock_response, monkeypatch):
    """Test finding an installed solution pack"""
    expected_response = {
        "@context": "/api/3/contexts/SolutionPack",
        "hydra:member": [{
            "name": "SOAR Framework",
            "label": "SOAR Framework",
            "version": "1.0.0",
            "installed": True
        }]
    }

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=expected_response)
    )

    result = mock_client.solution_packs.find_installed_pack("SOAR Framework")
    assert result["name"] == "SOAR Framework"
    assert result["installed"] is True
