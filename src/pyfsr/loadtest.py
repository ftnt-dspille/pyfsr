"""Concurrent load testing and outage window detection for playbook triggers.

Supports firing N playbook triggers in parallel while the caller separately
calls publish(), tracking success/failure + latency per call with timestamps.
Detects first failure, first recovery, and calculates outage window duration.

Example:
    >>> from pyfsr import FortiSOAR
    >>> from pyfsr.loadtest import OutageWindowLoadHelper
    >>> client = FortiSOAR("https://soar.example.com", api_key="...")
    >>> helper = OutageWindowLoadHelper(client, "Block IP", num_threads=5)
    >>> results = helper.run_load_test(num_iterations=100, interval=1.0)
    >>> print(f"Outage: {results.outage_duration_seconds}s")
    >>> print(f"Success rate: {results.success_rate:.1f}%")
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .exceptions import FortiSOARException

if TYPE_CHECKING:
    from .client import FortiSOAR


@dataclass
class TrialResult:
    """Result of a single playbook trigger trial.

    Attributes:
        trial_num: Sequence number of this trial.
        timestamp: UTC datetime when the trigger was fired.
        success: Whether the trigger succeeded.
        latency_seconds: Elapsed time for the trigger call (seconds).
        error_message: Error message if success=False, else None.
    """

    trial_num: int
    timestamp: datetime
    success: bool
    latency_seconds: float
    error_message: str | None = None


@dataclass
class OutageWindow:
    """Outage window analysis for concurrent load testing.

    Tracks the first failure timestamp, first recovery timestamp (after a failure),
    and the duration between them. Also accumulates overall success/failure counts
    and per-trial latency records.

    Attributes:
        first_failure_timestamp: UTC datetime of the first failed trigger, or None.
        first_recovery_timestamp: UTC datetime of the first successful trigger after
            a failure (i.e., after first_failure_timestamp), or None.
        outage_duration_seconds: Duration (seconds) between first failure and first
            recovery, or None if no recovery yet.
        total_trials: Total number of trigger attempts.
        successful_trials: Count of successful triggers.
        failed_trials: Count of failed triggers.
        results: All trial results in order.
    """

    first_failure_timestamp: datetime | None = None
    first_recovery_timestamp: datetime | None = None
    outage_duration_seconds: float | None = None
    total_trials: int = 0
    successful_trials: int = 0
    failed_trials: int = 0
    results: list[TrialResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """Return percentage of successful trials (0–100), or 0 if no trials."""
        if self.total_trials == 0:
            return 0.0
        return (self.successful_trials / self.total_trials) * 100

    def add_result(self, result: TrialResult) -> None:
        """Add a trial result and update outage window tracking.

        Updates success/failure counts and detects outage windows. On the first
        failure it records ``first_failure_timestamp``; on the first success after
        a failure it records ``first_recovery_timestamp`` and calculates
        ``outage_duration_seconds``.

        Args:
            result: A TrialResult to add.
        """
        self.results.append(result)
        self.total_trials += 1

        if result.success:
            self.successful_trials += 1
            # If we had failures but now recovered, record recovery time
            if self.first_failure_timestamp and not self.first_recovery_timestamp:
                self.first_recovery_timestamp = result.timestamp
                delta = result.timestamp - self.first_failure_timestamp
                self.outage_duration_seconds = delta.total_seconds()
        else:
            self.failed_trials += 1
            # Record first failure time
            if not self.first_failure_timestamp:
                self.first_failure_timestamp = result.timestamp


class OutageWindowLoadHelper:
    """Concurrent load tester for playbook trigger outage detection.

    Fires N playbook triggers in parallel at a specified interval, tracking
    per-call latency and success/failure with timestamps. Detects the outage
    window (first failure and first recovery) and calculates duration.

    The caller is responsible for calling publish() (or other appliance changes)
    in a separate context; this helper only measures trigger behavior before,
    during, and after the outage.

    Example:
        >>> helper = OutageWindowLoadHelper(client, "Block IP", num_threads=5)
        >>> results = helper.run_load_test(
        ...     num_iterations=100,
        ...     interval=0.5,
        ...     timeout=10.0,
        ... )
        >>> if results.first_failure_timestamp:
        ...     print(f"Outage detected: {results.outage_duration_seconds}s")
        ... else:
        ...     print("No outage detected")
    """

    def __init__(
        self,
        client: FortiSOAR,
        playbook: str,
        *,
        num_threads: int = 5,
    ) -> None:
        """Initialize the load helper.

        Args:
            client: An initialized FortiSOAR client (with auth configured).
            playbook: The playbook UUID or name to trigger. Must have a manual
                or API-endpoint trigger.
            num_threads: Number of concurrent trigger threads (default 5).
                Distributes num_iterations across these threads.
        """
        self.client = client
        self.playbook = playbook
        self.num_threads = num_threads
        self._results_lock = threading.Lock()
        self._results = OutageWindow()

    def run_load_test(
        self,
        num_iterations: int = 100,
        interval: float = 1.0,
        timeout: float = 10.0,
        on_trial_complete: Callable[[TrialResult], None] | None = None,
    ) -> OutageWindow:
        """Run the concurrent load test.

        Launches num_threads concurrent threads, each pulling from a shared
        counter and firing playbook triggers at the specified interval. Returns
        after all iterations complete.

        Args:
            num_iterations: Total number of triggers to fire across all threads.
            interval: Delay (seconds) between each trigger fire within a thread.
            timeout: Per-trigger timeout (seconds).
            on_trial_complete: Optional callback fired after each trial result
                completes (receives a TrialResult). Useful for progress reporting.

        Returns:
            An OutageWindow with success/failure tracking, latency records,
            and outage duration (if any failures were detected).
        """
        self._results = OutageWindow()
        trial_counter = 0
        counter_lock = threading.Lock()

        def worker() -> None:
            nonlocal trial_counter
            while True:
                # Grab the next trial number atomically
                with counter_lock:
                    if trial_counter >= num_iterations:
                        break
                    trial_num = trial_counter
                    trial_counter += 1

                # Run the trial
                result = self._run_trial(trial_num, timeout)

                # Record result (thread-safe)
                with self._results_lock:
                    self._results.add_result(result)

                # Invoke callback if provided
                if on_trial_complete:
                    on_trial_complete(result)

                # Wait before next iteration
                time.sleep(interval)

        # Launch threads and wait for completion
        threads = [threading.Thread(target=worker, daemon=False) for _ in range(self.num_threads)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        return self._results

    def _run_trial(self, trial_num: int, timeout: float) -> TrialResult:
        """Execute a single trigger trial and record the result.

        Args:
            trial_num: Trial sequence number.
            timeout: Per-trigger timeout (seconds).

        Returns:
            A TrialResult with timestamp, latency, and success/error info.
        """
        timestamp = datetime.now(timezone.utc)
        start = time.time()
        error_msg = None
        success = False

        try:
            self.client.playbooks.trigger(
                self.playbook,
                timeout=timeout,
            )
            success = True
        except FortiSOARException as e:
            error_msg = str(e)
        except Exception as e:
            # Capture non-FortiSOAR exceptions (network, timeout, etc.)
            error_msg = f"{type(e).__name__}: {e}"

        latency = time.time() - start

        return TrialResult(
            trial_num=trial_num,
            timestamp=timestamp,
            success=success,
            latency_seconds=latency,
            error_message=error_msg,
        )
