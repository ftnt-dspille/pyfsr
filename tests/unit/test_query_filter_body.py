"""check_filter_body: the cases the appliance silently widens or 500s on (live matrix)."""

import pytest

from pyfsr.exceptions import ValidationError
from pyfsr.query_models import check_filter_body


def test_missing_logic_becomes_and_at_every_level():
    body = check_filter_body({"filters": [{"filters": [{"field": "name", "operator": "eq", "value": "x"}]}]})
    assert body["logic"] == "AND" and body["filters"][0]["logic"] == "AND"


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"logic": "XOR", "filters": []}, "AND or OR"),
        ({"logic": "AND", "filters": [{"operator": "eq", "value": 1}]}, "needs both"),
        ({"logic": "AND", "filters": [{"field": "name", "value": 1}]}, "needs both"),
        ({"logic": "AND", "filters": [{"field": "name", "operator": "equals", "value": 1}]}, "use 'eq'"),
        ({"logic": "AND", "filters": [{"field": "name", "operator": "nope", "value": 1}]}, "unknown operator"),
        (
            {"logic": "AND", "filters": [{"logic": "AND", "filters": [{"field": "name"}]}]},
            r"filters\[0\]\.filters\[0\]",
        ),
        ({"logic": "AND", "filters": "name=x"}, "must be a list"),
    ],
)
def test_refused(body, why):
    with pytest.raises(ValidationError, match=why):
        check_filter_body(body)


def test_extra_leaf_keys_and_lowercase_logic_pass():
    body = check_filter_body(
        {"logic": "or", "filters": [{"field": "status", "operator": "eq", "value": "x", "type": "primitive"}]}
    )
    assert body["logic"] == "OR" and body["filters"][0]["type"] == "primitive"


def test_a_body_without_filters_is_untouched():
    assert check_filter_body({"sort": [{"field": "id"}]}) == {"sort": [{"field": "id"}]}
