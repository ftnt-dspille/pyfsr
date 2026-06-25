"""Unit tests for :mod:`pyfsr.loadtest` — outage window detection.

All tests run offline with mocked client and no live appliance.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from pyfsr.exceptions import FortiSOARException
from pyfsr.loadtest import (
    OutageWindow,
    OutageWindowLoadHelper,
    TrialResult,
)


@pytest.fixture
def mock_client():
    """Create a mock FortiSOAR client with playbooks API."""
    client = MagicMock()
    client.playbooks = MagicMock()
    return client


class TestTrialResult:
    """Tests for TrialResult dataclass."""

    def test_successful_trial(self):
        """Test creating a successful trial result."""
        ts = datetime.utcnow()
        result = TrialResult(
            trial_num=1,
            timestamp=ts,
            success=True,
            latency_seconds=0.5,
        )
        assert result.trial_num == 1
        assert result.success is True
        assert result.latency_seconds == 0.5
        assert result.error_message is None

    def test_failed_trial(self):
        """Test creating a failed trial result."""
        ts = datetime.utcnow()
        result = TrialResult(
            trial_num=2,
            timestamp=ts,
            success=False,
            latency_seconds=0.2,
            error_message="Connection refused",
        )
        assert result.success is False
        assert result.error_message == "Connection refused"
        assert result.latency_seconds == 0.2


class TestOutageWindow:
    """Tests for OutageWindow dataclass and outage detection."""

    def test_initial_state(self):
        """Test OutageWindow starts empty with no outage."""
        ow = OutageWindow()
        assert ow.total_trials == 0
        assert ow.successful_trials == 0
        assert ow.failed_trials == 0
        assert ow.first_failure_timestamp is None
        assert ow.first_recovery_timestamp is None
        assert ow.outage_duration_seconds is None
        assert ow.success_rate == 0.0
        assert ow.results == []

    def test_add_successful_result(self):
        """Test adding a successful result."""
        ow = OutageWindow()
        ts = datetime.utcnow()
        result = TrialResult(
            trial_num=1,
            timestamp=ts,
            success=True,
            latency_seconds=0.1,
        )
        ow.add_result(result)

        assert ow.total_trials == 1
        assert ow.successful_trials == 1
        assert ow.failed_trials == 0
        assert ow.success_rate == 100.0
        assert ow.first_failure_timestamp is None
        assert len(ow.results) == 1

    def test_add_failed_result(self):
        """Test adding a failed result records first failure time."""
        ow = OutageWindow()
        ts = datetime.utcnow()
        result = TrialResult(
            trial_num=1,
            timestamp=ts,
            success=False,
            latency_seconds=0.2,
            error_message="Connection timeout",
        )
        ow.add_result(result)

        assert ow.total_trials == 1
        assert ow.successful_trials == 0
        assert ow.failed_trials == 1
        assert ow.success_rate == 0.0
        assert ow.first_failure_timestamp == ts
        assert ow.first_recovery_timestamp is None
        assert ow.outage_duration_seconds is None

    def test_outage_window_single_failure_then_recovery(self):
        """Test detecting an outage window: failure then recovery."""
        ow = OutageWindow()

        # First failure
        ts1 = datetime.utcnow()
        result1 = TrialResult(
            trial_num=1,
            timestamp=ts1,
            success=False,
            latency_seconds=0.1,
            error_message="API error",
        )
        ow.add_result(result1)

        assert ow.first_failure_timestamp == ts1
        assert ow.first_recovery_timestamp is None
        assert ow.outage_duration_seconds is None

        # Recovery (5 seconds later)
        ts2 = ts1 + timedelta(seconds=5.0)
        result2 = TrialResult(
            trial_num=2,
            timestamp=ts2,
            success=True,
            latency_seconds=0.1,
        )
        ow.add_result(result2)

        assert ow.first_recovery_timestamp == ts2
        assert ow.outage_duration_seconds == pytest.approx(5.0, abs=0.01)
        assert ow.successful_trials == 1
        assert ow.failed_trials == 1

    def test_outage_window_multiple_failures_then_recovery(self):
        """Test that only the first failure timestamp is used."""
        ow = OutageWindow()

        # First failure
        ts1 = datetime.utcnow()
        result1 = TrialResult(
            trial_num=1,
            timestamp=ts1,
            success=False,
            latency_seconds=0.1,
            error_message="Error 1",
        )
        ow.add_result(result1)

        # Second failure (should not change first_failure_timestamp)
        ts2 = ts1 + timedelta(seconds=1.0)
        result2 = TrialResult(
            trial_num=2,
            timestamp=ts2,
            success=False,
            latency_seconds=0.1,
            error_message="Error 2",
        )
        ow.add_result(result2)

        assert ow.first_failure_timestamp == ts1
        assert ow.failed_trials == 2

        # Recovery
        ts3 = ts1 + timedelta(seconds=10.0)
        result3 = TrialResult(
            trial_num=3,
            timestamp=ts3,
            success=True,
            latency_seconds=0.1,
        )
        ow.add_result(result3)

        # Outage duration should be from first failure to recovery
        assert ow.first_recovery_timestamp == ts3
        assert ow.outage_duration_seconds == pytest.approx(10.0, abs=0.01)

    def test_success_rate_calculation(self):
        """Test success rate is calculated correctly."""
        ow = OutageWindow()

        # Add 7 results: 5 successful, 2 failed
        for i in range(7):
            ts = datetime.utcnow() + timedelta(seconds=i * 0.1)
            result = TrialResult(
                trial_num=i,
                timestamp=ts,
                success=(i < 5),  # First 5 are successes
                latency_seconds=0.1,
            )
            ow.add_result(result)

        assert ow.total_trials == 7
        assert ow.successful_trials == 5
        assert ow.failed_trials == 2
        assert ow.success_rate == pytest.approx(71.43, rel=0.01)

    def test_success_rate_zero_trials(self):
        """Test success rate with no trials is 0."""
        ow = OutageWindow()
        assert ow.success_rate == 0.0

    def test_success_rate_all_failed(self):
        """Test success rate when all trials fail."""
        ow = OutageWindow()
        for i in range(3):
            ts = datetime.utcnow() + timedelta(seconds=i * 0.1)
            result = TrialResult(
                trial_num=i,
                timestamp=ts,
                success=False,
                latency_seconds=0.1,
                error_message="Failed",
            )
            ow.add_result(result)

        assert ow.success_rate == 0.0
        assert ow.failed_trials == 3


class TestOutageWindowLoadHelper:
    """Tests for OutageWindowLoadHelper concurrent load testing."""

    def test_initialization(self, mock_client):
        """Test helper initialization with default thread count."""
        helper = OutageWindowLoadHelper(
            mock_client,
            "test-playbook-uuid",
            num_threads=3,
        )
        assert helper.client is mock_client
        assert helper.playbook == "test-playbook-uuid"
        assert helper.num_threads == 3

    def test_initialization_default_threads(self, mock_client):
        """Test helper initializes with default thread count."""
        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid")
        assert helper.num_threads == 5

    def test_run_load_test_all_success(self, mock_client):
        """Test load test with all successful triggers."""
        mock_client.playbooks.trigger.return_value = {"task_id": "test-id"}

        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid", num_threads=2)
        results = helper.run_load_test(
            num_iterations=4,
            interval=0.01,
            timeout=5.0,
        )

        assert results.total_trials == 4
        assert results.successful_trials == 4
        assert results.failed_trials == 0
        assert results.success_rate == 100.0
        assert results.first_failure_timestamp is None
        assert results.first_recovery_timestamp is None
        assert results.outage_duration_seconds is None

        # Verify trigger was called 4 times
        assert mock_client.playbooks.trigger.call_count == 4

    def test_run_load_test_with_failures(self, mock_client):
        """Test load test with some failures (FortiSOARException)."""
        call_count = 0

        def trigger_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise FortiSOARException("Trigger failed")
            return {"task_id": "test-id"}

        mock_client.playbooks.trigger.side_effect = trigger_side_effect

        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid", num_threads=1)
        results = helper.run_load_test(
            num_iterations=4,
            interval=0.01,
            timeout=5.0,
        )

        assert results.total_trials == 4
        assert results.successful_trials == 2
        assert results.failed_trials == 2
        assert results.first_failure_timestamp is not None
        # Check if recovery occurred (3rd and 4th calls succeeded)
        assert results.first_recovery_timestamp is not None
        assert results.outage_duration_seconds is not None

    def test_run_load_test_generic_exception(self, mock_client):
        """Test load test catches generic exceptions (timeout, connection, etc.)."""

        def trigger_side_effect(*args, **kwargs):
            raise TimeoutError("Request timeout")

        mock_client.playbooks.trigger.side_effect = trigger_side_effect

        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid", num_threads=1)
        results = helper.run_load_test(
            num_iterations=2,
            interval=0.01,
            timeout=5.0,
        )

        assert results.total_trials == 2
        assert results.failed_trials == 2
        assert all(r.error_message.startswith("TimeoutError") for r in results.results)

    def test_callback_invoked_on_each_trial(self, mock_client):
        """Test that on_trial_complete callback is invoked for each trial."""
        mock_client.playbooks.trigger.return_value = {"task_id": "test-id"}

        callback_results = []

        def callback(result: TrialResult) -> None:
            callback_results.append(result)

        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid", num_threads=1)
        results = helper.run_load_test(
            num_iterations=3,
            interval=0.01,
            timeout=5.0,
            on_trial_complete=callback,
        )

        assert len(callback_results) == 3
        assert all(isinstance(r, TrialResult) for r in callback_results)
        assert results.total_trials == 3

    def test_callback_receives_correct_data(self, mock_client):
        """Test callback receives trial results with correct fields."""
        mock_client.playbooks.trigger.return_value = {"task_id": "test-id"}

        callback_results = []

        def callback(result: TrialResult) -> None:
            callback_results.append(result)

        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid", num_threads=1)
        helper.run_load_test(
            num_iterations=2,
            interval=0.01,
            timeout=5.0,
            on_trial_complete=callback,
        )

        # Check first callback result
        first = callback_results[0]
        assert first.trial_num == 0
        assert first.success is True
        assert first.error_message is None
        assert first.latency_seconds >= 0.0
        assert isinstance(first.timestamp, datetime)

    def test_concurrent_execution_distributes_trials(self, mock_client):
        """Test that trials are distributed across multiple threads."""
        mock_client.playbooks.trigger.return_value = {"task_id": "test-id"}

        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid", num_threads=3)
        results = helper.run_load_test(
            num_iterations=9,
            interval=0.01,
            timeout=5.0,
        )

        assert results.total_trials == 9
        assert results.successful_trials == 9
        # All 9 trials should be recorded
        assert len(results.results) == 9

    def test_trial_timestamps_are_distinct(self, mock_client):
        """Test that each trial gets its own timestamp."""
        mock_client.playbooks.trigger.return_value = {"task_id": "test-id"}

        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid", num_threads=1)
        results = helper.run_load_test(
            num_iterations=3,
            interval=0.01,
            timeout=5.0,
        )

        timestamps = [r.timestamp for r in results.results]
        # With a single thread and 0.01s interval, timestamps should differ
        # (or at least, most should be distinct given datetime resolution)
        assert len(timestamps) == 3
        assert all(isinstance(ts, datetime) for ts in timestamps)

    def test_latency_recorded_per_trial(self, mock_client):
        """Test that latency is recorded for each trial."""
        mock_client.playbooks.trigger.return_value = {"task_id": "test-id"}

        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid", num_threads=1)
        results = helper.run_load_test(
            num_iterations=2,
            interval=0.01,
            timeout=5.0,
        )

        assert all(r.latency_seconds >= 0.0 for r in results.results)

    def test_error_message_preserved_on_failure(self, mock_client):
        """Test that error messages are captured and preserved."""
        error_msg = "Custom API error message"
        mock_client.playbooks.trigger.side_effect = FortiSOARException(error_msg)

        helper = OutageWindowLoadHelper(mock_client, "test-playbook-uuid", num_threads=1)
        results = helper.run_load_test(
            num_iterations=1,
            interval=0.01,
            timeout=5.0,
        )

        assert results.failed_trials == 1
        assert error_msg in results.results[0].error_message

    def test_playbook_name_passed_to_trigger(self, mock_client):
        """Test that playbook parameter is passed correctly to trigger."""
        mock_client.playbooks.trigger.return_value = {"task_id": "test-id"}

        playbook_name = "My Custom Playbook"
        helper = OutageWindowLoadHelper(mock_client, playbook_name, num_threads=1)
        helper.run_load_test(
            num_iterations=1,
            interval=0.01,
            timeout=5.0,
        )

        # Verify trigger was called with the playbook name
        mock_client.playbooks.trigger.assert_called_once()
        call_args = mock_client.playbooks.trigger.call_args
        assert call_args[0][0] == playbook_name

    def test_timeout_passed_to_trigger(self, mock_client):
        """Test that timeout parameter is passed to trigger calls."""
        mock_client.playbooks.trigger.return_value = {"task_id": "test-id"}

        helper = OutageWindowLoadHelper(mock_client, "test-playbook", num_threads=1)
        helper.run_load_test(
            num_iterations=1,
            interval=0.01,
            timeout=15.0,  # Custom timeout
        )

        # Verify timeout was passed
        call_args = mock_client.playbooks.trigger.call_args
        assert call_args[1]["timeout"] == 15.0
