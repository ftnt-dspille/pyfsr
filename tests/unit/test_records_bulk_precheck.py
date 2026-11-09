"""Bulk writes report rows the pre-write check refuses, per row, when parse=True."""

from __future__ import annotations

import pytest

from pyfsr._testing import demo_client_jwt
from pyfsr.exceptions import ValidationError

ROWS = [{"name": "good alert", "severity": "Low"}, {"name": "bad alert", "severity": "not-a-real-severity"}]


def test_a_refused_row_is_a_failed_row_and_the_rest_is_sent():
    result = demo_client_jwt().records("alerts").bulk_upsert(ROWS, parse=True)
    refused = [f for f in result.failed if f.message.startswith("refused before writing")]
    assert [f.index for f in refused] == [1] and "severity" in refused[0].message
    assert result.succeeded  # the good row was still sent


def test_without_parse_a_refused_row_raises_before_sending():
    with pytest.raises(ValidationError):
        demo_client_jwt().records("alerts").bulk_upsert(ROWS)


def test_server_failure_indexes_map_back_to_the_callers_rows():
    from pyfsr.records import RecordSet

    raw = {"failure": ["POST method for object at index #0 in the request payload failed with error: x"]}
    failed = RecordSet._bulk_failures(raw, positions=[2], refused=[])
    assert failed[0].index == 2
