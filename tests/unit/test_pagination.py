"""Unit tests for Hydra pagination helpers."""

from pyfsr import HydraPage, paginate
from pyfsr.pagination import extract_members, extract_total


def _envelope(members, total=None, next_url=None):
    env = {"hydra:member": members}
    if total is not None:
        env["hydra:totalItems"] = total
    if next_url is not None:
        env["hydra:view"] = {"hydra:next": next_url}
    return env


def test_extract_members_and_total():
    env = _envelope([{"a": 1}], total=42)
    assert extract_members(env) == [{"a": 1}]
    assert extract_total(env) == 42


def test_extract_members_tolerates_bare_list_and_garbage():
    assert extract_members([{"a": 1}]) == [{"a": 1}]
    assert extract_members("nope") == []
    assert extract_total({"no": "total"}) is None


def test_hydrapage_from_response():
    page = HydraPage.from_response(_envelope([1, 2, 3], total=3), page=1, limit=30)
    assert page.count == 3
    assert page.total == 3
    assert list(page) == [1, 2, 3]
    assert len(page) == 3


def test_has_next_via_view():
    page = HydraPage.from_response(_envelope([1], next_url="/api/3/x?$page=2"), limit=30)
    assert page.has_next is True


def test_has_next_count_heuristic():
    full = HydraPage.from_response(_envelope([1, 2]), page=1, limit=2)
    assert full.has_next is True
    partial = HydraPage.from_response(_envelope([1]), page=1, limit=2)
    assert partial.has_next is False


def test_paginate_walks_until_empty():
    pages = {
        1: _envelope([1, 2]),
        2: _envelope([3, 4]),
        3: _envelope([5]),  # short page → stop after
    }
    got = list(paginate(lambda p: pages.get(p, _envelope([])), page_size=2))
    assert got == [1, 2, 3, 4, 5]


def test_paginate_stops_on_empty_page():
    pages = {1: _envelope([1, 2]), 2: _envelope([])}
    got = list(paginate(lambda p: pages.get(p, _envelope([])), page_size=2))
    assert got == [1, 2]


def test_paginate_respects_max_records():
    pages = {p: _envelope([p * 10, p * 10 + 1]) for p in range(1, 10)}
    got = list(paginate(lambda p: pages[p], page_size=2, max_records=3))
    assert got == [10, 11, 20]


def test_paginate_prefetch_preserves_order():
    pages = {
        1: _envelope([1, 2]),
        2: _envelope([3, 4]),
        3: _envelope([5]),  # short page → stop after
    }
    got = list(paginate(lambda p: pages.get(p, _envelope([])), page_size=2, prefetch=2))
    assert got == [1, 2, 3, 4, 5]


def test_paginate_prefetch_respects_max_records():
    pages = {p: _envelope([p * 10, p * 10 + 1]) for p in range(1, 10)}
    got = list(paginate(lambda p: pages[p], page_size=2, max_records=3, prefetch=3))
    assert got == [10, 11, 20]


def test_paginate_prefetch_stops_on_empty_page():
    pages = {1: _envelope([1, 2]), 2: _envelope([])}
    got = list(paginate(lambda p: pages.get(p, _envelope([])), page_size=2, prefetch=2))
    assert got == [1, 2]
