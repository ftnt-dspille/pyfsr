"""Maximum concurrent playbook execution analysis.

Computes the maximum number of simultaneously in-flight playbook executions
given a list of runs with start and end timestamps. This is a pure utility for
proving loop max-parallel caps in 1273958 Part B.

The :func:`compute_overlap` function takes a list of execution runs (dicts or
dict-like objects from :meth:`~pyfsr.api.playbooks.PlaybooksAPI.execution_history`
or :meth:`~pyfsr.api.playbooks.PlaybooksAPI.get_execution`) and sweeps their
timestamps to find the peak concurrency and generate a timeline of concurrency
events. Timestamps can be ISO8601 strings or Unix epoch floats; missing end
times are treated as "still running" (extends to the end of the observation
window).

Example::

    from pyfsr import FortiSOAR
    from pyfsr.concurrency import compute_overlap

    client = FortiSOAR(...)
    runs = client.playbooks.execution_history(playbook="Child PB", limit=100)
    result = compute_overlap(runs)
    print(f"Max concurrent: {result['max_concurrent']}")
    print(f"Timeline events: {result['events']}")
    # On FortiSOAR 8.0 with loop max-parallel=2, expect max_concurrent <= 2
    # On 7.6.5 (no cap), all runs execute simultaneously
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel

__all__ = ["compute_overlap", "ConcurrencyResult"]


class ConcurrencyResult(BaseModel):
    """Result of a concurrency analysis.

    Attributes:
        max_concurrent: The maximum number of runs in-flight simultaneously.
        events: Timeline of concurrency events, sorted by time. Each event is a
            dict with keys: ``time`` (ISO8601 string), ``count`` (int, current
            concurrent after the event), ``reason`` (string describing the change).
        run_count: Total number of runs analyzed.
    """

    max_concurrent: int
    events: list[dict[str, Any]]
    run_count: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict (compatible with ApiResult).

        Alias for ``model_dump()``, kept for back-compat with callers that
        predate the pydantic conversion.
        """
        return self.model_dump()


def _parse_timestamp(ts: Any) -> datetime | None:
    """Parse a timestamp to a datetime.

    Accepts:
    - ISO8601 strings (e.g. "2026-06-08T12:30:45Z" or "2026-06-08T12:30:45.123456")
    - Unix epoch floats (seconds, e.g. 1717929045.0)
    - datetime objects
    - None (treated as invalid)

    Returns None if parsing fails.
    """
    if ts is None:
        return None

    if isinstance(ts, datetime):
        # Ensure it's timezone-aware (assume UTC if naive)
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    if isinstance(ts, (int, float)):
        # Unix epoch
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            return None

    if isinstance(ts, str):
        # ISO8601. fromisoformat handles 'Z' and offsets on Python 3.11+; normalise
        # the trailing 'Z' for older patch levels. A parsed value with no offset is
        # assumed UTC so all comparisons stay timezone-aware.
        dt_str = ts.strip().replace("Z", "+00:00") if ts else ""
        try:
            parsed = datetime.fromisoformat(dt_str)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    return None


def _resolve_timestamp_field(run: dict[str, Any], field_names: list[str]) -> Any | None:
    """Try multiple field name variants to extract a timestamp from a run dict.

    Tries each name in order; returns the first non-None value found.
    """
    for name in field_names:
        val = run.get(name)
        if val is not None:
            return val
    return None


def compute_overlap(
    runs: list[dict[str, Any]] | list[Any],
    *,
    start_field: str | list[str] | None = None,
    end_field: str | list[str] | None = None,
) -> ConcurrencyResult:
    """Compute maximum concurrent playbook executions from a list of runs.

    This is the classic interval max-overlap sweep algorithm. Creates events for
    each run's start (+1) and end (-1), sorts by time, and sweeps to find the
    peak concurrency. Runs without an end time are treated as "still running"
    (extended to the maximum observed time).

    Args:
        runs: List of execution run dicts (or dict-like objects, e.g. from
            :meth:`~pyfsr.api.playbooks.PlaybooksAPI.execution_history`). Each
            run should have a start and (optionally) end timestamp field.
        start_field: Field name(s) to extract the start timestamp. If a list,
            tries each in order until one is found. Defaults to trying:
            ``["startDate", "start_time", "start", "createDate", "created"]``.
        end_field: Field name(s) to extract the end timestamp. If a list, tries
            each in order. Defaults to trying:
            ``["endDate", "end_time", "end", "modifyDate", "modified", "completedOnDate"]``.

    Returns:
        A :class:`ConcurrencyResult` with:
        - ``max_concurrent``: The peak count of simultaneous runs.
        - ``events``: Timeline of concurrency-changing events (sorted by time).
        - ``run_count``: The number of runs with valid start times (processed).

    Raises:
        ValueError: If no runs have valid start times.

    Example::

        from pyfsr.concurrency import compute_overlap
        from pyfsr import FortiSOAR

        client = FortiSOAR(...)
        runs = client.playbooks.execution_history(playbook="Child", limit=50)
        result = compute_overlap(runs)
        assert result.max_concurrent <= 2, "Loop max-parallel cap failed"
    """
    if not runs:
        return ConcurrencyResult(max_concurrent=0, events=[], run_count=0)

    # Resolve field names
    if start_field is None:
        start_fields = ["startDate", "start_time", "start", "createDate", "created"]
    elif isinstance(start_field, str):
        start_fields = [start_field]
    else:
        start_fields = start_field

    if end_field is None:
        end_fields = ["endDate", "end_time", "end", "modifyDate", "modified", "completedOnDate"]
    elif isinstance(end_field, str):
        end_fields = [end_field]
    else:
        end_fields = end_field

    # Extract and parse timestamps
    events: list[tuple[datetime, int, str]] = []  # (time, delta, description)
    max_time: datetime | None = None
    run_count = 0
    ongoing_runs_indices: list[int] = []  # Track which runs never ended

    for i, run in enumerate(runs):
        if not isinstance(run, dict):
            # Try to convert dict-like to dict (e.g., ApiResult)
            try:
                run = dict(run)
            except (TypeError, ValueError):
                continue

        # Extract start time
        start_val = _resolve_timestamp_field(run, start_fields)
        start_dt = _parse_timestamp(start_val)
        if start_dt is None:
            continue

        run_count += 1

        # Extract end time (optional)
        end_val = _resolve_timestamp_field(run, end_fields)
        end_dt = _parse_timestamp(end_val)

        # Create start event
        run_id = f"run{i}"
        events.append((start_dt, 1, f"+1 start {run_id}"))

        # Update max_time
        if max_time is None:
            max_time = start_dt
        if end_dt is not None and end_dt > max_time:
            max_time = end_dt
        elif end_dt is not None:
            # Even if end_dt <= max_time, use it to update max_time for this run
            pass
        else:
            # No end time: track for implicit end later
            ongoing_runs_indices.append(i)
            if max_time is None or start_dt > max_time:
                max_time = start_dt

        # Create end event (if the run actually ended AND we parsed the timestamp).
        # Gate on end_dt, not end_val: a malformed end_val (non-None but unparseable)
        # is already treated as "no end time" by the elif/else above and gets an
        # implicit end at max_time; appending a None here would crash the later
        # event_time.isoformat() call.
        if end_dt is not None:
            events.append((end_dt, -1, f"-1 end {run_id}"))
        # else: run has no usable end time (absent or malformed), gets an
        # implicit end at max_time via ongoing_runs_indices.

    if run_count == 0:
        raise ValueError("No runs with valid start timestamps found")

    if max_time is None:
        max_time = events[0][0] if events else datetime.now(tz=timezone.utc)

    # Add implicit end events for runs without explicit end times.
    # Add them at max_time + 1 second to ensure they appear after any starts at max_time.
    implicit_end_time = max_time + timedelta(seconds=1)
    for idx in ongoing_runs_indices:
        events.append((implicit_end_time, -1, f"-1 end-implicit run{idx}"))

    # Sort events by time, then by sign (ends before starts at same time to avoid
    # false overlap when one run ends exactly when another starts)
    events.sort(key=lambda x: (x[0], x[1]))

    # Sweep and build timeline
    current_count = 0
    max_concurrent = 0
    timeline: list[dict[str, Any]] = []

    for event_time, delta, reason in events:
        current_count += delta
        if current_count > max_concurrent:
            max_concurrent = current_count
        timeline.append(
            {
                "time": event_time.isoformat(),
                "count": current_count,
                "reason": reason,
            }
        )

    return ConcurrencyResult(
        max_concurrent=max_concurrent,
        events=timeline,
        run_count=run_count,
    )
