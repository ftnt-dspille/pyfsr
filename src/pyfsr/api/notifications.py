"""System (bell-icon) notifications -- ``/api/rule/api/system-notification/notifications/``.

The per-user notifications the platform raises for record events (task
assignments, approvals, SLA breaches, ...). Accessed as ``client.notifications``.

This is a ``rule`` API entity, not a ``/api/3`` module: the listing is fetched
with ``POST`` (the UI posts to the endpoint), returns a hydra envelope
(``hydra:member`` + ``hydra:totalItems`` + paging links), and each row is a
:class:`~pyfsr.models.Notification`. ``id_iri``/``record_type`` stay ``None`` --
use ``uuid`` as the identity.

Example:
    >>> unread = client.notifications.list(read=False)
    >>> unread[0].content
    '<p>A task, Notify asset owner ...</p>'
    >>> client.notifications.count(read=False)
    18206
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..models._system import Notification, NotificationPurge
from ..pagination import paginate_offset
from .base import BaseAPI

_ENDPOINT = "/api/rule/api/system-notification/notifications/"
_PURGE = "/api/rule/api/system-notification/purge/"


def _entity_filter(entity_type: str | Sequence[str] | None) -> str | None:
    """Normalize ``entity_type`` to the endpoint's comma-joined ``__in`` value."""
    if entity_type is None:
        return None
    if isinstance(entity_type, str):
        return entity_type
    return ",".join(entity_type)


class NotificationsAPI(BaseAPI):
    """List, count, and page through the caller's system notifications."""

    def list(
        self,
        *,
        read: bool | None = None,
        entity_type: str | Sequence[str] | None = None,
        search: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        typed: bool = True,
    ) -> list[Notification] | list[dict[str, Any]]:
        """Return the caller's notifications, newest first.

        Args:
            read: filter by read state -- ``False`` for unread only, ``True`` for
                read only, ``None`` (default) for all.
            entity_type: restrict to one or more source entity types (the
                endpoint's ``entity_type__in`` filter), e.g. ``"comments"`` or
                ``["approvals", "manualinput"]``. ``None`` (default) for all types.
            search: free-text filter over the notification ``content`` (the
                endpoint's ``search`` query param); ``None`` (default) for no filter.
            limit: page size. ``None`` (default) fetches every page (following
                ``hydra:nextPage`` until exhausted); pass an int for a single page.
            offset: starting offset for a single-page fetch (ignored when
                ``limit`` is ``None`` and all pages are pulled).
            typed: parse rows into :class:`~pyfsr.models.Notification` (default);
                pass ``False`` for raw dicts.

        Example:
            >>> client.notifications.list(read=False, entity_type="comments")
            >>> client.notifications.list(entity_type=["approvals", "manualinput"])

        Doctest:

            >>> from pyfsr._testing import demo_client
            >>> client = demo_client()
            >>> notifications = client.notifications.list(limit=10)
            >>> len(notifications)
            2
            >>> notifications[0].entity_type
            'tasks'
        """
        params: dict[str, Any] = {"format": "json"}
        if read is not None:
            params["read"] = str(read).lower()
        entity_in = _entity_filter(entity_type)
        if entity_in is not None:
            params["entity_type__in"] = entity_in
        if search is not None:
            params["search"] = search

        if limit is not None:
            params["limit"] = limit
            params["offset"] = offset
            members = self._members(self.client.post(_ENDPOINT, params=params))
        else:
            members = paginate_offset(
                lambda page_offset: self.client.post(_ENDPOINT, params=dict(params, offset=page_offset)),
                offset=offset,
            )

        if typed:
            return [Notification.model_validate(m) for m in members]
        return members

    def count(
        self,
        *,
        read: bool | None = None,
        entity_type: str | Sequence[str] | None = None,
        search: str | None = None,
    ) -> int:
        """Total notification count (``hydra:totalItems``) for the given filters.

        A single-row fetch -- only the envelope total is used, not the members.
        """
        params: dict[str, Any] = {"format": "json", "limit": 1, "offset": 0}
        if read is not None:
            params["read"] = str(read).lower()
        entity_in = _entity_filter(entity_type)
        if entity_in is not None:
            params["entity_type__in"] = entity_in
        if search is not None:
            params["search"] = search
        resp = self.client.post(_ENDPOINT, params=params)
        if isinstance(resp, dict):
            return int(resp.get("hydra:totalItems") or 0)
        return 0

    def purge(self, *, read: bool | None = None) -> NotificationPurge:
        """Bulk-delete notifications (``POST .../system-notification/purge/``).

        Kicks off an **asynchronous** server-side purge and returns immediately
        with a :class:`~pyfsr.models.NotificationPurge` ack (``result`` +
        ``status``); the rows are removed in the background, so a following
        :meth:`count` may still report the old total briefly.

        Args:
            read: scope the purge by read state -- ``True`` purges only already-read
                notifications (the safe default the UI's "clear read" uses),
                ``False`` purges unread, ``None`` purges all. **There is no undo.**

        Example:
            >>> client.notifications.purge(read=True)
            {'result': 'System Notification purge started', 'status': 'started'}
        """
        params: dict[str, Any] = {"format": "json"}
        if read is not None:
            params["read"] = str(read).lower()
        return NotificationPurge.model_validate(self.client.post(_PURGE, params=params))

    @staticmethod
    def _members(resp: Any) -> list[dict[str, Any]]:
        if isinstance(resp, dict):
            return resp.get("hydra:member") or resp.get("results") or []
        return resp or []
