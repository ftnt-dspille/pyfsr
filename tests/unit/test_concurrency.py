"""Unit tests for concurrency overlap analysis."""

from datetime import datetime, timezone

import pytest

from pyfsr.concurrency import _parse_timestamp, compute_overlap

# -- timestamp parsing ------------------------------------------------------


def test_parse_timestamp_iso8601_with_z():
    dt = _parse_timestamp("2026-06-08T12:30:45Z")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 6
    assert dt.day == 8
    assert dt.hour == 12
    assert dt.minute == 30
    assert dt.second == 45
    assert dt.tzinfo == timezone.utc


def test_parse_timestamp_iso8601_with_timezone():
    dt = _parse_timestamp("2026-06-08T12:30:45+00:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.tzinfo is not None


def test_parse_timestamp_iso8601_with_microseconds():
    dt = _parse_timestamp("2026-06-08T12:30:45.123456Z")
    assert dt is not None
    assert dt.microsecond == 123456


def test_parse_timestamp_unix_epoch_float():
    ts = 1717929045.0  # 2026-06-08 around that time
    dt = _parse_timestamp(ts)
    assert dt is not None
    assert isinstance(dt, datetime)
    assert dt.tzinfo == timezone.utc


def test_parse_timestamp_unix_epoch_int():
    ts = 1717929045
    dt = _parse_timestamp(ts)
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_parse_timestamp_datetime_object():
    orig = datetime(2026, 6, 8, 12, 30, 45)
    dt = _parse_timestamp(orig)
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_parse_timestamp_datetime_aware():
    orig = datetime(2026, 6, 8, 12, 30, 45, tzinfo=timezone.utc)
    dt = _parse_timestamp(orig)
    assert dt == orig


def test_parse_timestamp_none():
    dt = _parse_timestamp(None)
    assert dt is None


def test_parse_timestamp_invalid_string():
    dt = _parse_timestamp("not a timestamp")
    assert dt is None


def test_parse_timestamp_invalid_type():
    dt = _parse_timestamp({"not": "a timestamp"})
    assert dt is None


# -- concurrency computation ---------------------------------------------------


def test_compute_overlap_empty_list():
    result = compute_overlap([])
    assert result.max_concurrent == 0
    assert result.events == []
    assert result.run_count == 0


def test_compute_overlap_no_valid_timestamps():
    runs = [{"no_timestamp": "here"}, {"name": "also no timestamp"}]
    with pytest.raises(ValueError, match="No runs with valid start timestamps"):
        compute_overlap(runs)


def test_compute_overlap_single_run():
    runs = [
        {
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:01:00Z",
        }
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 1
    assert result.run_count == 1
    assert len(result.events) == 2  # start and end
    assert result.events[0]["count"] == 1  # after start
    assert result.events[1]["count"] == 0  # after end


def test_compute_overlap_two_sequential_runs():
    runs = [
        {
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:01:00Z",
        },
        {
            "startDate": "2026-06-08T12:01:00Z",
            "endDate": "2026-06-08T12:02:00Z",
        },
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 1
    assert result.run_count == 2
    # All events: start1, end1/start2 (at same time), end2
    # After start1: 1, after end1: 0, after start2: 1, after end2: 0
    assert result.max_concurrent == 1


def test_compute_overlap_two_concurrent_runs():
    runs = [
        {
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:02:00Z",
        },
        {
            "startDate": "2026-06-08T12:01:00Z",
            "endDate": "2026-06-08T12:03:00Z",
        },
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 2
    assert result.run_count == 2
    # After start1: 1, after start2: 2, after end1: 1, after end2: 0
    assert result.events[-1]["count"] == 0


def test_compute_overlap_three_with_max_two():
    runs = [
        {
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:02:00Z",
        },
        {
            "startDate": "2026-06-08T12:01:00Z",
            "endDate": "2026-06-08T12:02:00Z",
        },
        {
            "startDate": "2026-06-08T12:02:00Z",
            "endDate": "2026-06-08T12:03:00Z",
        },
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 2


def test_compute_overlap_alternative_field_names():
    # Using different field name conventions
    runs = [
        {
            "start_time": "2026-06-08T12:00:00Z",
            "end_time": "2026-06-08T12:01:00Z",
        }
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 1


def test_compute_overlap_unix_timestamps():
    # Using Unix epoch floats
    ts_start = 1717929045.0  # 2026-06-08 ~12:04 UTC
    ts_end = ts_start + 60  # 1 minute later
    runs = [
        {
            "startDate": ts_start,
            "endDate": ts_end,
        }
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 1


def test_compute_overlap_missing_end_time():
    # A run without an end time (still running)
    runs = [
        {
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:01:00Z",
        },
        {
            "startDate": "2026-06-08T12:00:30Z",
            # No end time
        },
    ]
    result = compute_overlap(runs)
    assert result.run_count == 2
    # Both should overlap, so max is 2
    assert result.max_concurrent == 2
    # The second run should have an implicit end at max_time
    assert result.events[-1]["count"] == 0


def test_compute_overlap_all_missing_end_times():
    # All runs have no end time
    runs = [
        {"startDate": "2026-06-08T12:00:00Z"},
        {"startDate": "2026-06-08T12:00:30Z"},
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 2


def test_compute_overlap_custom_field_names():
    runs = [
        {
            "begin": "2026-06-08T12:00:00Z",
            "finish": "2026-06-08T12:01:00Z",
        }
    ]
    result = compute_overlap(runs, start_field="begin", end_field="finish")
    assert result.max_concurrent == 1


def test_compute_overlap_custom_field_names_list():
    # Try multiple names in order
    runs = [
        {
            "customStart": "2026-06-08T12:00:00Z",
            "customEnd": "2026-06-08T12:01:00Z",
        }
    ]
    result = compute_overlap(
        runs,
        start_field=["customStart", "startDate"],
        end_field=["customEnd", "endDate"],
    )
    assert result.max_concurrent == 1


def test_compute_overlap_result_to_dict():
    runs = [
        {
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:01:00Z",
        }
    ]
    result = compute_overlap(runs)
    d = result.to_dict()
    assert isinstance(d, dict)
    assert "max_concurrent" in d
    assert "events" in d
    assert "run_count" in d
    assert d["max_concurrent"] == 1


def test_compute_overlap_timeline_correctness():
    runs = [
        {
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:02:00Z",
        },
        {
            "startDate": "2026-06-08T12:01:00Z",
            "endDate": "2026-06-08T12:03:00Z",
        },
    ]
    result = compute_overlap(runs)
    # Check that events are in chronological order
    times = [datetime.fromisoformat(e["time"]) for e in result.events]
    assert times == sorted(times)
    # Check counts make sense
    assert all(0 <= e["count"] for e in result.events)
    # Last event should be 0 (no concurrent runs)
    if result.events:
        assert result.events[-1]["count"] == 0


def test_compute_overlap_dict_like_object():
    # ApiResult or other dict-like objects should work
    class DictLike(dict):
        def __init__(self, data):
            super().__init__(data)

        def get(self, key, default=None):
            return super().get(key, default)

    runs = [
        DictLike(
            {
                "startDate": "2026-06-08T12:00:00Z",
                "endDate": "2026-06-08T12:01:00Z",
            }
        )
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 1


def test_compute_overlap_stress_many_concurrent():
    # Stress test: many runs all concurrent
    runs = [
        {
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:01:00Z",
        }
        for _ in range(100)
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 100
    assert result.run_count == 100


def test_compute_overlap_stress_many_sequential():
    # Stress test: many runs sequential
    runs = []
    base_ts = 1717929045.0  # Unix epoch start
    for i in range(100):
        start_ts = base_ts + (i * 60)  # Each run starts 60 seconds after the previous
        end_ts = start_ts + 30  # Each run lasts 30 seconds
        runs.append({"startDate": start_ts, "endDate": end_ts})
    result = compute_overlap(runs)
    assert result.max_concurrent == 1
    assert result.run_count == 100


def test_compute_overlap_realistic_playbook_runs():
    # Simulate realistic playbook execution history
    runs = [
        {
            "@id": "/api/wf/api/workflows/run1/",
            "name": "Block IP",
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:00:05Z",
            "status": "completed",
        },
        {
            "@id": "/api/wf/api/workflows/run2/",
            "name": "Block IP",
            "startDate": "2026-06-08T12:00:02Z",
            "endDate": "2026-06-08T12:00:07Z",
            "status": "completed",
        },
        {
            "@id": "/api/wf/api/workflows/run3/",
            "name": "Block IP",
            "startDate": "2026-06-08T12:00:07Z",
            "endDate": "2026-06-08T12:00:12Z",
            "status": "completed",
        },
    ]
    result = compute_overlap(runs)
    # run1 and run2 overlap: max 2
    assert result.max_concurrent == 2
    assert result.run_count == 3


def test_compute_overlap_preserves_other_fields():
    # Ensure that extra fields in runs don't break processing
    runs = [
        {
            "startDate": "2026-06-08T12:00:00Z",
            "endDate": "2026-06-08T12:01:00Z",
            "task_id": "t1",
            "status": "completed",
            "result": {"message": "ok"},
            "extra": {"nested": {"data": "value"}},
        }
    ]
    result = compute_overlap(runs)
    assert result.max_concurrent == 1
    assert result.run_count == 1
