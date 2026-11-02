"""Record values are fixed or refused by display type before a write.

Each case mirrors what a live appliance does with the raw value (see
``tests/integration/test_field_type_matrix_integration.py``): stores it wrong,
or answers with an error that points the wrong way.
"""

from datetime import datetime, timezone

import pytest

from pyfsr.exceptions import ValidationError
from pyfsr.field_values import check_record, check_value
from pyfsr.records import RecordSet

ISO = "2026-01-02T03:04:05Z"
EPOCH = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp())


@pytest.mark.parametrize(
    ("form", "value", "sent", "note"),
    [
        ("datetime", ISO, EPOCH, "ISO date -> epoch seconds"),
        ("datetime", EPOCH * 1000, EPOCH, "epoch milliseconds -> seconds"),
        ("datetime", EPOCH, EPOCH, None),
        ("datetime", str(EPOCH), str(EPOCH), None),  # the box parses a digit string itself
        ("checkbox", "yes", True, "'yes' -> true"),
        ("checkbox", "no", False, "'no' -> false"),
        ("checkbox", False, False, None),
        ("integer", "5", 5, None),
        ("integer", 5.0, 5, None),
        ("ipv4", "198.51.100.23", "198.51.100.23", None),
        ("email", "a@example.com", "a@example.com", None),
        ("json", {"a": 1}, {"a": 1}, None),
        ("text", "anything", "anything", None),
        ("datetime", None, None, None),
    ],
)
def test_accepted_values(form, value, sent, note):
    got, got_note, problem = check_value(form, value)
    assert (got, got_note, problem) == (sent, note, None)


@pytest.mark.parametrize(
    ("form", "value", "why"),
    [
        ("datetime", "next tuesday", "not a date"),
        ("checkbox", "maybe", "not true/false"),
        ("integer", 3_000_000_000, "32-bit"),
        ("integer", 1.5, "whole number"),
        ("ipv4", "999.1.1.1", "IPV4"),
        ("ipv6", "198.51.100.23", "IPV6"),
        ("email", "not-an-email", "valid email"),
        ("url", "not a url", "valid url"),
        ("json", "not json", "object or list"),
    ],
)
def test_refused_values(form, value, why):
    _, _, problem = check_value(form, value)
    assert why in problem


def test_check_record_names_every_bad_field_at_once():
    with pytest.raises(ValidationError) as exc:
        check_record({"a": "integer", "b": "checkbox"}, {"a": 1.5, "b": "maybe", "c": "x"}, module="m")
    assert "a:" in str(exc.value) and "b:" in str(exc.value) and "for m" in str(exc.value)


class _Modules:
    def describe(self, module):
        return {"fields": [{"name": "seen", "form_type": "datetime"}, {"name": "flag", "form_type": "checkbox"}]}


class _Client:
    def __init__(self):
        self.modules = _Modules()
        self.posted = None

    def post(self, path, data=None):
        self.posted = data
        return {"@id": "/api/3/m/u1", **data}


def test_create_converts_and_reports():
    c = _Client()
    rs = RecordSet(c, "m")
    rs.create({"seen": ISO, "flag": "yes"}, raw=True, resolve_picklists=False)
    assert c.posted == {"seen": EPOCH, "flag": True}
    assert rs.last_conversions == ["seen: ISO date -> epoch seconds", "flag: 'yes' -> true"]


def test_create_refuses_before_sending():
    c = _Client()
    with pytest.raises(ValidationError, match="flag"):
        RecordSet(c, "m").create({"flag": "maybe"}, raw=True, resolve_picklists=False)
    assert c.posted is None


def test_check_values_false_sends_as_given():
    c = _Client()
    RecordSet(c, "m", check_values=False).create({"flag": "maybe"}, raw=True, resolve_picklists=False)
    assert c.posted == {"flag": "maybe"}
