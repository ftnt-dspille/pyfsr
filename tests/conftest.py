import importlib.util
import json
from pathlib import Path

import pytest
import requests
from requests import Response

# Optional-extra gating -------------------------------------------------------
#
# Tests that need an optional extra declare it once:
#
#     @pytest.mark.requires_extra("playbooks")     # one test
#     pytestmark = pytest.mark.requires_extra("playbooks")   # whole module
#
# and the hook below turns a missing extra into a *skip*. This is the single
# source of truth: gating by hand (bare imports, per-file `skipif`, ad-hoc
# `importorskip`) is what let three modules hard-fail with ModuleNotFoundError
# on an install without the extra, while CI's install step assumed they would
# skip. The `no-extras` CI job runs the suite on a bare install to keep that
# assumption enforced rather than merely intended.
#
# Maps the extra name in pyproject's [project.optional-dependencies] to the
# module that proves it is installed.
EXTRA_IMPORTS = {
    "playbooks": "fsr_playbooks",
    "mcp": "mcp",
}


def _extra_missing(extra: str) -> bool:
    """True when ``extra``'s proof module cannot be imported."""
    try:
        module = EXTRA_IMPORTS[extra]
    except KeyError:
        raise pytest.UsageError(
            f"unknown extra {extra!r} in requires_extra; add it to EXTRA_IMPORTS in tests/conftest.py"
        ) from None
    return importlib.util.find_spec(module) is None


def pytest_collection_modifyitems(config, items):
    """Skip tests marked ``requires_extra`` when that extra is not installed."""
    for item in items:
        for mark in item.iter_markers(name="requires_extra"):
            for extra in mark.args:
                if _extra_missing(extra):
                    item.add_marker(
                        pytest.mark.skip(reason=f"requires the {extra!r} extra (pip install 'pyfsr[{extra}]')")
                    )


@pytest.fixture
def mock_auth_response(mock_response):
    """Mock successful auth response"""
    return mock_response(
        json_data={
            "token": "mock-jwt-token-123"  # Match FortiSOAR response format
        }
    )


@pytest.fixture
def mock_response():
    """Create a mock response with custom status code and data."""

    def _mock_response(status_code=200, json_data=None, raise_error=None):
        response = Response()
        response.status_code = status_code

        # Allow passing a dict that will be returned as json
        if json_data is not None:
            response._content = json.dumps(json_data).encode("utf-8")
            response.json = lambda: json_data

        # Set up raise_for_status behavior
        def raise_for_status():
            if status_code >= 400:
                raise requests.exceptions.HTTPError(f"HTTP Error {status_code}", response=response)

        response.raise_for_status = raise_for_status
        return response

    return _mock_response


@pytest.fixture
def mock_client(mock_response, monkeypatch):
    """Create a FortiSOAR client instance for testing with mocked requests."""
    from requests.sessions import Session

    from pyfsr import FortiSOAR

    def mock_auth_request(self, method, url, **kwargs):
        if "/auth/authenticate" in url:
            return mock_response(json_data={"token": "mock-token-123"})
        return mock_response()  # Default 200 response for other requests

    # Mock the `requests.Session.request` method
    monkeypatch.setattr(Session, "request", mock_auth_request)

    # Create client with mocked session
    client = FortiSOAR(
        base_url="https://test.fortisoar.com",
        username="test_user",
        password="test_pass",
        verify_ssl=False,
        suppress_insecure_warnings=True,
    )

    return client


@pytest.fixture
def mock_responses():
    """Load mock response data from JSON files."""

    def load_mock_response(filename):
        path = Path(__file__).parent / "resources" / "mock_responses" / filename
        with open(path) as f:
            return json.load(f)

    return load_mock_response


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "integration: mark test as requiring integration with real FortiSOAR instance")
    config.addinivalue_line(
        "markers",
        "requires_extra(name): skip unless the named optional extra (see EXTRA_IMPORTS) is installed",
    )
