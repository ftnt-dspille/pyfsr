"""Tests for the Alerts API functionality."""
import json
from pathlib import Path

import pytest

from pyfsr.exceptions import ValidationError


def load_mock_response(filename):
    """Helper to load mock response data"""
    path = Path(__file__).parent.parent / 'resources' / 'mock_responses' / filename
    with open(path) as f:
        return json.load(f)


def test_create_alert_success(mock_client, mock_response, monkeypatch):
    """Test successful alert creation"""
    mock_data = load_mock_response('alert_create_response.json')

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=mock_data)
    )

    alert_data = {
        "name": "Response Capture Test Alert",
        "description": "Test alert for mock response",
        "severity": "/api/3/picklists/58d0753f-f7e4-403b-953c-b0f521eab759"
    }

    result = mock_client.alerts.create(**alert_data)
    assert result["@type"] == "Alert"
    assert result["name"] == alert_data["name"]


def test_get_alert(mock_client, mock_response, monkeypatch):
    """Test retrieving a single alert"""
    mock_data = load_mock_response('alert_get_response.json')

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=mock_data)
    )

    alert_id = mock_data["@id"].split("/")[-1]
    result = mock_client.alerts.get(alert_id)

    assert result["@type"] == "Alert"
    assert result["@id"] == mock_data["@id"]
    assert result["name"] == mock_data["name"]


def test_list_alerts(mock_client, mock_response, monkeypatch):
    """Test listing alerts with pagination"""
    mock_data = load_mock_response('alert_list_response.json')

    monkeypatch.setattr(
        "requests.Session.request",
        lambda *args, **kwargs: mock_response(json_data=mock_data)
    )

    result = mock_client.alerts.list()

    assert result["@context"] == "/api/3/contexts/Alert"
    assert result["@type"] == "hydra:Collection"
    assert isinstance(result["hydra:member"], list)
    assert all(alert["@type"] == "Alert" for alert in result["hydra:member"])


def test_create_alert_validation_error(mock_client, mock_response, monkeypatch):
    """Test alert creation with invalid data"""
    error_response = {
        "type": "ValidationException",
        "message": "name: This value should not be blank."
    }

    def mock_request(*args, **kwargs):
        # For alert creation, return validation error
        return mock_response(status_code=400, json_data=error_response)

    monkeypatch.setattr("requests.Session.request", mock_request)

    # Test creating alert with missing name (required field)
    with pytest.raises(ValidationError) as exc_info:
        mock_client.alerts.create(description="Test Alert")

    assert "name: This value should not be blank" in str(exc_info.value)


def test_create_alert_with_picklist_values(mock_client, mock_response, monkeypatch):
    """Test creating an alert with proper picklist references"""
    mock_data = load_mock_response('alert_create_response.json')
    severity_data = load_mock_response('alert_severity_picklist.json')
    status_data = load_mock_response('alert_status_picklist.json')

    responses = {
        'severity': severity_data,
        'status': status_data,
        'create': mock_data
    }

    def mock_request(*args, **kwargs):
        url = kwargs.get('url', '')
        if 'picklists' in url:
            if 'severity' in url.lower():
                return mock_response(json_data=responses['severity']['@id'])
            return mock_response(json_data=responses['status']['@id'])
        return mock_response(json_data=responses['create'])

    monkeypatch.setattr("requests.Session.request", mock_request)

    alert_data = {
        "name": "Test Alert",
        "description": "Alert with picklist values",
        "severity": "/api/3/picklists/58d0753f-f7e4-403b-953c-b0f521eab759",
        "status": "/api/3/picklists/7de816ff-7140-4ee5-bd05-93ce22002146"
    }

    result = mock_client.alerts.create(**alert_data)
    assert result["@type"] == "Alert"
    assert result["severity"]['@id'] == alert_data["severity"]
    assert result["status"]['@id'] == alert_data["status"]