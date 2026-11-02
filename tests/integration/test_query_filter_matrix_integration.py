"""What the query API does with a filter it cannot read (opt-in: pytest -m integration).

Sends each malformed filter straight to ``POST /api/query/alerts`` and compares the
total with an unfiltered query and with a filter that matches nothing. A result
equal to the unfiltered total means the filter was silently dropped: a typo then
reads as "every record matches". Read-only.
"""

import pytest

pytestmark = pytest.mark.integration

NOTHING = "pyfsr-no-such-alert-name-0"
CASES = [
    ("unknown field", {"logic": "AND", "filters": [{"field": "nosuchfield", "operator": "eq", "value": NOTHING}]}),
    ("misspelt field", {"logic": "AND", "filters": [{"field": "nmae", "operator": "eq", "value": NOTHING}]}),
    ("missing operator", {"logic": "AND", "filters": [{"field": "name", "value": NOTHING}]}),
    ("missing field", {"logic": "AND", "filters": [{"operator": "eq", "value": NOTHING}]}),
    ("unknown operator", {"logic": "AND", "filters": [{"field": "name", "operator": "equals", "value": NOTHING}]}),
    ("bad logic", {"logic": "XOR", "filters": [{"field": "name", "operator": "eq", "value": NOTHING}]}),
    ("no logic", {"filters": [{"field": "name", "operator": "eq", "value": NOTHING}]}),
    (
        "unknown field in group",
        {
            "logic": "AND",
            "filters": [{"logic": "AND", "filters": [{"field": "nosuchfield", "operator": "eq", "value": NOTHING}]}],
        },
    ),
]


def _total(client, body):
    try:
        resp = client.post("/api/query/alerts", data=body, params={"$limit": 1})
    except Exception as exc:  # noqa: BLE001 - a refusal is the observation
        return f"refused: {str(exc).splitlines()[0][:100]}"
    return resp.get("hydra:totalItems")


def test_filter_matrix(client, capsys):
    everything = _total(client, {"logic": "AND", "filters": []})
    none = _total(client, {"logic": "AND", "filters": [{"field": "name", "operator": "eq", "value": NOTHING}]})
    assert none == 0 and isinstance(everything, int) and everything > 0
    rows = []
    for case, body in CASES:
        got = _total(client, body)
        verdict = "DROPPED (returns everything)" if got == everything else ("applied" if got == 0 else f"other ({got})")
        rows.append((case, got, verdict))
    with capsys.disabled():
        print(f"\nquery filter matrix (unfiltered total {everything}):")
        for case, got, verdict in rows:
            print(f"  {case:24} -> {got!s:60} {verdict}")


@pytest.mark.parametrize(("case", "body"), CASES)
def test_pyfsr_never_widens_a_bad_filter(client, case, body):
    """Through records().query, each case is refused or applied, never widened to everything."""
    import copy

    from pyfsr.exceptions import FortiSOARException

    try:
        page = client.records("alerts").query({**copy.deepcopy(body), "limit": 1}, raw=True)
    except FortiSOARException as exc:
        assert "500" not in str(exc) and "Internal Server Error" not in str(exc), f"{case}: {exc}"
        return
    total = page.total if hasattr(page, "total") else page.get("hydra:totalItems")
    assert total == 0, f"{case}: returned {total} records (filter widened)"


def test_where_body_matches_what_the_raw_filters_miss(client):
    """A bare picklist eq and a bare like each match nothing; where_body lowers both so they match."""
    rs = client.records("alerts")
    raw = {"logic": "AND", "filters": [{"field": "status", "operator": "eq", "value": "Open"}]}
    assert _total(client, raw) == 0  # the trap where_body exists for
    body = rs.where_body("status=Open")
    assert body["filters"][0]["field"] == "status.itemValue"
    assert _total(client, body) > 0
    assert _total(client, rs.where_body("name~Agent Lab")) > 0
