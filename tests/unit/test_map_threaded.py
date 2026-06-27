"""Unit tests for the shared map_threaded fan-out primitive (pyfsr._concurrency)."""

import threading

import pytest

from pyfsr._concurrency import map_threaded


def test_preserves_input_order():
    # Even with uneven per-item work, results follow input order.
    assert map_threaded(lambda n: n * 10, [1, 2, 3, 4, 5]) == [10, 20, 30, 40, 50]


def test_empty_input():
    assert map_threaded(lambda x: x, []) == []


def test_runs_concurrently():
    # 4 items that all block on the same barrier only complete if they truly
    # run in parallel; the barrier's timeout fails the test otherwise.
    barrier = threading.Barrier(4, timeout=5)

    def work(_):
        barrier.wait()
        return "done"

    assert map_threaded(work, range(4), max_workers=4) == ["done"] * 4


def test_on_error_none_substitutes_none():
    def work(n):
        if n == 2:
            raise ValueError("boom")
        return n

    assert map_threaded(work, [1, 2, 3]) == [1, None, 3]


def test_on_error_raise_propagates():
    def work(n):
        if n == 2:
            raise ValueError("boom")
        return n

    with pytest.raises(ValueError, match="boom"):
        map_threaded(work, [1, 2, 3], on_error="raise")


def test_single_worker_path():
    # max_workers=1 takes the serial branch but keeps the same contract.
    assert map_threaded(lambda n: n + 1, [1, 2, 3], max_workers=1) == [2, 3, 4]


def test_invalid_on_error_rejected():
    with pytest.raises(ValueError):
        map_threaded(lambda x: x, [1], on_error="explode")
