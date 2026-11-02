"""Audit window dates (live proof: test_audit_dates_integration.py)."""

import datetime as dt

import pytest

from pyfsr.api.audit import _to_epoch_ms

MS = 1893456000000  # 2030-01-01T00:00:00Z


@pytest.mark.parametrize(
    "val",
    [
        1893456000,
        MS,
        "1893456000",
        str(MS),
        "2030-01-01T00:00:00Z",
        "2030-01-01T00:00:00",
        "2030-01-01",
        dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
    ],
)
def test_every_form_reaches_the_same_instant(val):
    assert _to_epoch_ms(val) == MS


@pytest.mark.parametrize("val", ["yesterday", "", True, [1]])
def test_a_value_that_is_not_a_date_is_refused(val):
    with pytest.raises(ValueError, match="not a date"):
        _to_epoch_ms(val)


def test_none_is_no_bound():
    assert _to_epoch_ms(None) is None
