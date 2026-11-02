"""Cron next-fire and the audit's pending verdict (live proof: test_schedule_fire_integration.py)."""

import datetime as dt

import pytest

from pyfsr.api.schedules import FIRE_GRACE_SECONDS, _first_fire_due, next_fire

T0 = dt.datetime(2026, 3, 2, 10, 30, 15)  # a Monday


@pytest.mark.parametrize(
    ("cron", "want"),
    [
        ("* * * * *", dt.datetime(2026, 3, 2, 10, 31)),
        ("*/15 * * * *", dt.datetime(2026, 3, 2, 10, 45)),
        ("0 * * * *", dt.datetime(2026, 3, 2, 11, 0)),
        ("0 2 * * *", dt.datetime(2026, 3, 3, 2, 0)),
        ("0 9 * * 0", dt.datetime(2026, 3, 8, 9, 0)),  # Sunday
        ("0 9 * * 7", dt.datetime(2026, 3, 8, 9, 0)),  # 7 is Sunday too
        ("0 9 * * 1-5", dt.datetime(2026, 3, 3, 9, 0)),
        ("30 10 1 * 1", dt.datetime(2026, 3, 9, 10, 30)),  # dom OR dow
    ],
)
def test_next_fire(cron, want):
    assert next_fire(cron, T0) == want


def test_a_rare_cron_beyond_the_horizon_is_unknown():
    assert next_fire("0 0 29 2 *", T0) is None
    assert next_fire("not a cron", T0) is None


def test_first_fire_due_adds_the_grace_in_utc():
    row = {"date_changed": "2026-03-02T10:30:15Z"}
    due = _first_fire_due(row, "0 * * * *", "UTC")
    assert due == dt.datetime(2026, 3, 2, 11, 0, tzinfo=dt.UTC) + dt.timedelta(seconds=FIRE_GRACE_SECONDS)
    assert _first_fire_due({}, "0 * * * *", "UTC") is None


def test_a_run_from_before_the_schedule_is_not_a_fire():
    from pyfsr.api.schedules import _after

    assert _after("2026-03-02T10:31:00Z", "2026-03-02T10:30:15Z")
    assert not _after("2026-03-01T09:00:00Z", "2026-03-02T10:30:15Z")
    assert _after("t1", "2026-03-02T10:30:15Z")  # unreadable: not evidence against
