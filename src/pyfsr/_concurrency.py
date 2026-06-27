"""Shared concurrency primitive for fan-out reads.

pyfsr is ``requests``-based (synchronous). A ``requests.Session`` is safe for
concurrent *calls* (separate request/response cycles), so the pragmatic way to
speed up fan-out reads — warming N connector definitions, fetching a batch of
records by id, running healthchecks — is a bounded thread pool, not an async
rewrite.

:func:`map_threaded` is the single primitive every fan-out should use, so the
worker bound and the exception policy live in one place.

.. warning::
    Never share one :class:`sqlite3.Connection` across the worker threads. The
    established pattern is **fetch-parallel, write-serial**: run the network I/O
    through :func:`map_threaded`, then write the results from the calling thread.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: Default worker ceiling. Small enough to stay friendly to the appliance, large
#: enough to collapse a few dozen one-RTT calls into a couple of RTTs.
DEFAULT_MAX_WORKERS = 8


def map_threaded(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    on_error: str = "none",
) -> list[R | None]:
    """Apply ``fn`` to each item concurrently, preserving input order.

    Results are returned in the same order as ``items`` regardless of which
    worker finished first — callers can ``zip(items, results)`` safely.

    Args:
        fn: the per-item function. Called once per item, on a worker thread.
        items: the inputs. Materialised into a list so order is stable and the
            length is known up front.
        max_workers: thread ceiling. The pool is sized to ``min(max_workers,
            len(items))`` so a tiny batch doesn't spin up idle threads.
        on_error: what to do when ``fn`` raises —
            ``"none"`` (default) substitutes ``None`` for that item and keeps
            going; ``"raise"`` re-raises the first exception.

    Returns:
        A list the same length as ``items``. Entries where ``fn`` raised are
        ``None`` under the default ``on_error="none"`` policy.
    """
    work = list(items)
    if not work:
        return []
    if on_error not in ("none", "raise"):
        raise ValueError(f"on_error must be 'none' or 'raise', got {on_error!r}")

    def _safe(item: T) -> R | None:
        try:
            return fn(item)
        except Exception:
            if on_error == "raise":
                raise
            return None

    workers = min(max_workers, len(work))
    if workers <= 1:
        return [_safe(item) for item in work]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        # executor.map preserves input order and propagates exceptions on
        # iteration — _safe has already applied the chosen error policy.
        return list(ex.map(_safe, work))
