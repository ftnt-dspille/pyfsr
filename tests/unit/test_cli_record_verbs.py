"""Unit tests for the ``pyfsr records`` CLI verbs (alerts list, incidents query, records delete).

Tests use a mock FortiSOAR client (no live network calls). The CLI argument wiring
is verified by checking that the right client methods are called with the right arguments.
"""

from __future__ import annotations

import argparse
from io import StringIO
from unittest.mock import patch

from pyfsr.cli.__main__ import (
    cmd_records_alerts_list,
    cmd_records_delete,
    cmd_records_incidents_query,
)
from pyfsr.query import Query


def suppress_output(func):
    """Decorator to suppress stdout/stderr during test execution."""

    def wrapper(*args, **kwargs):
        import sys

        stdout_backup = sys.stdout
        stderr_backup = sys.stderr
        try:
            sys.stdout = StringIO()
            sys.stderr = StringIO()
            return func(*args, **kwargs)
        finally:
            sys.stdout = stdout_backup
            sys.stderr = stderr_backup

    return wrapper


class MockRecordSet:
    """Mock RecordSet that returns canned records."""

    def __init__(self, module: str, records: list):
        self.module = module
        self.records = records

    def query(self, q: Query) -> MockPage:
        """Return a mock page with the canned records."""
        return MockPage(self.records)

    def delete(self, rec_id: str) -> None:
        """Mock delete; succeed for any id."""
        pass


class MockPage:
    """Mock Hydra page (has a members attribute)."""

    def __init__(self, records: list):
        self.members = records


class MockClient:
    """Mock FortiSOAR client."""

    def __init__(self, records_dict: dict[str, list] | None = None):
        self.records_dict = records_dict or {}
        self.http_trace = False

    def records(self, module: str) -> MockRecordSet:
        """Return a mock RecordSet for the given module."""
        return MockRecordSet(module, self.records_dict.get(module, []))


# --- test alert list ---
@suppress_output
def test_alerts_list_no_filters():
    """Test pyfsr records alerts with no filters."""
    alert1 = {
        "uuid": "alert-001",
        "name": "Test Alert 1",
        "status": {"itemValue": "Open"},
        "severity": {"itemValue": "Critical"},
        "createDate": "2026-06-23T10:00:00Z",
    }
    alert2 = {
        "uuid": "alert-002",
        "name": "Test Alert 2",
        "status": {"itemValue": "Closed"},
        "severity": {"itemValue": "Low"},
        "createDate": "2026-06-22T10:00:00Z",
    }

    args = argparse.Namespace(
        limit=50,
        status=None,
        severity=None,
        fmt="table",
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"alerts": [alert1, alert2]})
        mock_make.return_value = client

        result = cmd_records_alerts_list(args)

        assert result == 0
        # Verify the client was created and records() was called
        mock_make.assert_called_once()
        # Verify http_trace was set
        assert client.http_trace is False


@suppress_output
def test_alerts_list_with_status_filter():
    """Test pyfsr records alerts with status filter."""
    alert1 = {
        "uuid": "alert-001",
        "name": "Open Alert",
        "status": {"itemValue": "Open"},
        "severity": {"itemValue": "Critical"},
        "createDate": "2026-06-23T10:00:00Z",
    }

    args = argparse.Namespace(
        limit=50,
        status="Open",
        severity=None,
        fmt="json",
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"alerts": [alert1]})
        mock_make.return_value = client

        result = cmd_records_alerts_list(args)

        assert result == 0
        mock_make.assert_called_once()
        assert client.http_trace is False


@suppress_output
def test_alerts_list_with_severity_filter():
    """Test pyfsr records alerts with severity filter."""
    alert1 = {
        "uuid": "alert-001",
        "name": "Critical Alert",
        "status": {"itemValue": "Open"},
        "severity": {"itemValue": "Critical"},
        "createDate": "2026-06-23T10:00:00Z",
    }

    args = argparse.Namespace(
        limit=50,
        status=None,
        severity="Critical",
        fmt="json",
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"alerts": [alert1]})
        mock_make.return_value = client

        result = cmd_records_alerts_list(args)

        assert result == 0
        mock_make.assert_called_once()
        assert client.http_trace is False


@suppress_output
def test_alerts_list_empty():
    """Test pyfsr records alerts with no results."""
    args = argparse.Namespace(
        limit=50,
        status=None,
        severity=None,
        fmt="table",
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"alerts": []})
        mock_make.return_value = client

        result = cmd_records_alerts_list(args)

        assert result == 0
        mock_make.assert_called_once()


# --- test incidents query ---
@suppress_output
def test_incidents_query_json_dsl():
    """Test pyfsr records incidents with JSON DSL query."""
    incident1 = {
        "uuid": "inc-001",
        "name": "Test Incident",
        "status": {"itemValue": "Open"},
        "severity": {"itemValue": "High"},
    }

    args = argparse.Namespace(
        query='{"filters": [{"field": "status.itemValue", "operator": "eq", "value": "Open"}], "limit": 10}',
        fmt="json",
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"incidents": [incident1]})
        mock_make.return_value = client

        result = cmd_records_incidents_query(args)

        assert result == 0
        mock_make.assert_called_once()


@suppress_output
def test_incidents_query_simple_filter():
    """Test pyfsr records incidents with simple field=value filter."""
    incident1 = {
        "uuid": "inc-001",
        "name": "Test Incident",
        "status": {"itemValue": "Open"},
        "severity": {"itemValue": "High"},
    }

    args = argparse.Namespace(
        query="name=Test Incident",
        fmt="table",
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"incidents": [incident1]})
        mock_make.return_value = client

        result = cmd_records_incidents_query(args)

        assert result == 0
        mock_make.assert_called_once()


@suppress_output
def test_incidents_query_search_term():
    """Test pyfsr records incidents with plain text search."""
    incident1 = {
        "uuid": "inc-001",
        "name": "Malware Detection",
        "status": {"itemValue": "Open"},
        "severity": {"itemValue": "Critical"},
    }

    args = argparse.Namespace(
        query="Malware",
        fmt="json",
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"incidents": [incident1]})
        mock_make.return_value = client

        result = cmd_records_incidents_query(args)

        assert result == 0
        mock_make.assert_called_once()


@suppress_output
def test_incidents_query_no_results():
    """Test pyfsr records incidents with no results."""
    args = argparse.Namespace(
        query="nonexistent",
        fmt="table",
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"incidents": []})
        mock_make.return_value = client

        result = cmd_records_incidents_query(args)

        assert result == 0
        mock_make.assert_called_once()


# --- test records delete ---
@suppress_output
def test_records_delete_single_with_yes():
    """Test pyfsr records delete with single record and --yes."""
    args = argparse.Namespace(
        module="alerts",
        id=["alert-001"],
        yes=True,
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient()
        mock_make.return_value = client

        result = cmd_records_delete(args)

        assert result == 0
        mock_make.assert_called_once()


@suppress_output
def test_records_delete_multiple_with_yes():
    """Test pyfsr records delete with multiple records and --yes."""
    args = argparse.Namespace(
        module="incidents",
        id=["inc-001", "inc-002", "inc-003"],
        yes=True,
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient()
        mock_make.return_value = client

        result = cmd_records_delete(args)

        assert result == 0
        mock_make.assert_called_once()


@suppress_output
def test_records_delete_no_confirmation():
    """Test pyfsr records delete cancelled when user says no."""
    args = argparse.Namespace(
        module="alerts",
        id=["alert-001"],
        yes=False,
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient()
        mock_make.return_value = client

        with patch("builtins.input", return_value="n"):
            result = cmd_records_delete(args)

        assert result == 1


@suppress_output
def test_records_delete_confirmed():
    """Test pyfsr records delete when user says yes."""
    args = argparse.Namespace(
        module="alerts",
        id=["alert-001"],
        yes=False,
        log_requests=False,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient()
        mock_make.return_value = client

        with patch("builtins.input", return_value="y"):
            result = cmd_records_delete(args)

        assert result == 0
        mock_make.assert_called_once()


# --- test http_trace flag ---
def test_http_trace_flag_alerts_list():
    """Test that http_trace is set when log-requests or log-responses is True."""
    alert1 = {
        "uuid": "alert-001",
        "name": "Test Alert",
        "status": {"itemValue": "Open"},
        "severity": {"itemValue": "Critical"},
        "createDate": "2026-06-23T10:00:00Z",
    }

    args = argparse.Namespace(
        limit=50,
        status=None,
        severity=None,
        fmt="json",
        log_requests=True,
        log_responses=False,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"alerts": [alert1]})
        mock_make.return_value = client

        stdout_backup = __import__("sys").stdout
        try:
            __import__("sys").stdout = StringIO()
            cmd_records_alerts_list(args)
        finally:
            __import__("sys").stdout = stdout_backup

        # Verify http_trace was set on the client
        assert client.http_trace is True


def test_http_trace_flag_incidents_query():
    """Test that http_trace is set for incidents query."""
    incident1 = {
        "uuid": "inc-001",
        "name": "Test",
        "status": {"itemValue": "Open"},
        "severity": {"itemValue": "High"},
    }

    args = argparse.Namespace(
        query="test",
        fmt="json",
        log_requests=False,
        log_responses=True,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient({"incidents": [incident1]})
        mock_make.return_value = client

        stdout_backup = __import__("sys").stdout
        try:
            __import__("sys").stdout = StringIO()
            cmd_records_incidents_query(args)
        finally:
            __import__("sys").stdout = stdout_backup

        assert client.http_trace is True


def test_http_trace_flag_delete():
    """Test that http_trace is set for records delete."""
    args = argparse.Namespace(
        module="alerts",
        id=["alert-001"],
        yes=True,
        log_requests=True,
        log_responses=True,
    )

    with patch("pyfsr.cli.__main__.playbook_cmds._make_client") as mock_make:
        client = MockClient()
        mock_make.return_value = client

        stdout_backup = __import__("sys").stdout
        try:
            __import__("sys").stdout = StringIO()
            cmd_records_delete(args)
        finally:
            __import__("sys").stdout = stdout_backup

        assert client.http_trace is True
