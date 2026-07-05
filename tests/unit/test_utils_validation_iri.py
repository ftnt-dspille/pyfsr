"""Unit tests for the shared utils.validation / utils.iri helpers.

These replaced identical hand-rolled copies in roles.py/teams.py/playbooks.py/
modules_admin.py/workflow_collections.py (UUID check) and _record_module.py/
comments.py (module_from_iri) plus several .rsplit("/", 1)[-1] call sites
(uuid_from_iri).
"""

import pytest

from pyfsr.utils.iri import module_from_iri, uuid_from_iri
from pyfsr.utils.validation import is_uuid


@pytest.mark.parametrize(
    "value",
    [
        "3451141c-bac6-467c-8d72-85e0fab569ce",
        "3451141C-BAC6-467C-8D72-85E0FAB569CE",
        "  3451141c-bac6-467c-8d72-85e0fab569ce  ",
    ],
)
def test_is_uuid_accepts_valid_uuids(value):
    assert is_uuid(value) is True


@pytest.mark.parametrize(
    "value", ["", "not-a-uuid", "3451141c-bac6-467c-8d72", "/api/3/people/3451141c-bac6-467c-8d72-85e0fab569ce"]
)
def test_is_uuid_rejects_non_uuids(value):
    assert is_uuid(value) is False


def test_module_from_iri_extracts_second_to_last_segment():
    assert module_from_iri("/api/3/alerts/3451141c-bac6-467c-8d72-85e0fab569ce") == "alerts"


def test_module_from_iri_raises_on_too_few_segments():
    with pytest.raises(ValueError, match="Cannot derive module"):
        module_from_iri("alerts")


@pytest.mark.parametrize(
    "iri,expected",
    [
        ("/api/3/alerts/abc-123", "abc-123"),
        ("/api/3/alerts/abc-123/", "abc-123"),
        (None, None),
        ("", None),
    ],
)
def test_uuid_from_iri(iri, expected):
    assert uuid_from_iri(iri) == expected
