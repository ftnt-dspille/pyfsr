"""Helpers for FortiSOAR's Hydra (JSON-LD) collection envelopes.

List and query endpoints return::

    {
      "hydra:member": [ {record}, ... ],
      "hydra:totalItems": 1234,
      "hydra:view": { "hydra:next": "...", ... }
    }

``HydraPage`` wraps one such envelope; ``paginate`` walks every page lazily.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

MEMBER_KEY = "hydra:member"
TOTAL_KEY = "hydra:totalItems"
VIEW_KEY = "hydra:view"

T = TypeVar("T")


def extract_members(response: Any) -> list[Any]:
    """Return the ``hydra:member`` list from a response, or ``[]``.

    Tolerates a bare list (already-unwrapped) and non-dict responses.
    """
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        members = response.get(MEMBER_KEY)
        if isinstance(members, list):
            return members
    return []


def extract_total(response: Any) -> int | None:
    """Return ``hydra:totalItems`` if present, else ``None``."""
    if isinstance(response, dict):
        total = response.get(TOTAL_KEY)
        if isinstance(total, int):
            return total
    return None


@dataclass
class HydraPage(Generic[T]):
    """A single page of a Hydra collection.

    ``T`` is the element type — ``BaseRecord`` subclass when parsed by
    :class:`~pyfsr.records.RecordSet`, ``dict[str, Any]`` when raw.
    ``from_response`` always produces ``HydraPage[Any]``; ``RecordSet``
    narrows the type after parsing members.
    """

    #: The records on this page.
    members: list[T]
    #: ``hydra:totalItems`` across all pages (``None`` if absent).
    total: int | None
    #: 1-based page number this envelope represents.
    page: int
    #: Page size requested (``None`` if unknown).
    limit: int | None
    #: The full decoded response envelope.
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, response: Any, page: int = 1, limit: int | None = None) -> HydraPage[Any]:
        return cls(
            members=extract_members(response),
            total=extract_total(response),
            page=page,
            limit=limit,
            raw=response if isinstance(response, dict) else {},
        )

    @property
    def count(self) -> int:
        """Number of records on this page."""
        return len(self.members)

    @property
    def has_next(self) -> bool:
        """Whether another page is likely available.

        Prefers ``hydra:view.hydra:next``; falls back to a count heuristic
        (a full page implies there may be more).
        """
        view = self.raw.get(VIEW_KEY)
        if isinstance(view, dict) and view.get("hydra:next"):
            return True
        if self.limit is not None:
            return self.count >= self.limit
        return False

    def __iter__(self) -> Iterator[T]:
        return iter(self.members)

    def __len__(self) -> int:
        return len(self.members)


def paginate(
    fetch_page: Callable[[int], Any],
    *,
    page_size: int = 100,
    start_page: int = 1,
    max_records: int | None = None,
) -> Iterator[Any]:
    """Lazily yield every record across pages.

    Args:
        fetch_page: Callable taking a 1-based page number and returning the raw
            Hydra response for that page (the caller binds limit/query).
        page_size: Page size the caller requested; used for the count heuristic.
        start_page: First page to fetch.
        max_records: Optional ceiling on total records yielded.

    Yields:
        Individual records, page after page, stopping when a page is empty,
        ``has_next`` is false, or ``max_records`` is reached.
    """
    page = start_page
    yielded = 0
    while True:
        envelope = fetch_page(page)
        hp = HydraPage.from_response(envelope, page=page, limit=page_size)
        if not hp.members:
            return
        for record in hp.members:
            yield record
            yielded += 1
            if max_records is not None and yielded >= max_records:
                return
        if not hp.has_next:
            return
        page += 1
