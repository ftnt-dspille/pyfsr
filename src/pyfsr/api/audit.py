"""Audit-log gateway (``/api/gateway/audit``).

Query FortiSOAR's audit activity store by time window + filters, count matches,
fetch a single record, and list the valid operation values. Also exposes the two
documented retention controls (disable TTL auto-purge, and the risky wholesale
purge). Accessed as ``client.audit``.

The activities store uses **slice pagination**: responses carry no
``totalElements``/``totalPages`` — walk pages until one comes back empty.

Example:
    >>> client = demo_client()
    >>> result = client.audit.activities("2026-06-01T00:00:00Z", "2026-06-18T00:00:00Z",
    ...                                   operation="login", limit=100)
    >>> result["content"][0]["operation"]
    'create'
    >>> count = client.audit.count("2026-06-01T00:00:00Z", "2026-06-18T00:00:00Z")
    >>> count["count"]
    42
    >>> ops = client.audit.operations()
    >>> "login" in ops
    True
"""

from __future__ import annotations

from typing import Any

from .base import BaseAPI

_BASE = "/api/gateway/audit"


class AuditAPI(BaseAPI):
    """Query and manage FortiSOAR audit activity records."""

    def _filter_body(
        self,
        start_date: Any,
        end_date: Any,
        *,
        page: int | None,
        limit: int | None,
        operation: Any,
        component: str | None,
        user_id: str | None,
        entity_type: str | None,
        search: str | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"startDate": start_date, "endDate": end_date}
        for key, val in (
            ("page", page),
            ("limit", limit),
            ("operation", operation),
            ("component", component),
            ("userId", user_id),
            ("entityType", entity_type),
            ("search", search),
        ):
            if val is not None:
                body[key] = val
        return body

    def activities(
        self,
        start_date: Any,
        end_date: Any,
        *,
        page: int | None = None,
        limit: int | None = None,
        operation: Any = None,
        component: str | None = None,
        user_id: str | None = None,
        entity_type: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Query a slice of audit records (``POST /api/gateway/audit/activities``).

        ``start_date``/``end_date`` bound the window (required). Optional filters:
        ``operation`` (see :meth:`operations`), ``component``, ``user_id``,
        ``entity_type``, free-text ``search``, plus ``page``/``limit``. Slice
        pagination — keep paging until the result is empty (no total is returned).

        Example:
            >>> client = demo_client()
            >>> result = client.audit.activities("2026-06-01T00:00:00Z", "2026-06-18T00:00:00Z")
            >>> len(result["content"])
            1
            >>> result["content"][0]["user"]
            'admin'
        """
        body = self._filter_body(
            start_date,
            end_date,
            page=page,
            limit=limit,
            operation=operation,
            component=component,
            user_id=user_id,
            entity_type=entity_type,
            search=search,
        )
        return self.client.post(f"{_BASE}/activities", data=body)

    def count(
        self,
        start_date: Any,
        end_date: Any,
        *,
        operation: Any = None,
        component: str | None = None,
        user_id: str | None = None,
        entity_type: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Total audit-record count for a window + filters.

        ``POST /api/gateway/audit/activities/count`` — same filter body as
        :meth:`activities` (without paging).

        Example:
            >>> client = demo_client()
            >>> result = client.audit.count("2026-06-01T00:00:00Z", "2026-06-18T00:00:00Z")
            >>> result["count"]
            42
        """
        body = self._filter_body(
            start_date,
            end_date,
            page=None,
            limit=None,
            operation=operation,
            component=component,
            user_id=user_id,
            entity_type=entity_type,
            search=search,
        )
        return self.client.post(f"{_BASE}/activities/count", data=body)

    def get(self, audit_id: str) -> dict[str, Any]:
        """Fetch a single audit record by id
        (``GET /api/gateway/audit/activities/{audit_id}``).

        Example:
            >>> client = demo_client()
            >>> record = client.audit.get("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
            >>> record["operation"]
            'create'
            >>> record["component"]
            'alerts'
        """
        return self.client.get(f"{_BASE}/activities/{audit_id}")

    def operations(self) -> Any:
        """List the valid ``operation`` values (``GET /api/gateway/audit/operations``).

        The picklist to feed :meth:`activities`/:meth:`count` ``operation=``.

        Example:
            >>> client = demo_client()
            >>> ops = client.audit.operations()
            >>> "login" in ops
            True
            >>> "create" in ops
            True
        """
        return self.client.get(f"{_BASE}/operations")

    def disable_ttl(self) -> None:
        """Stop automatic purging of audit logs (``DELETE .../activities/ttl``).

        The documented Fortinet recipe to disable the audit-log TTL auto-purge.

        Example:
            >>> client = demo_client()
            >>> client.audit.disable_ttl()
        """
        self.client.delete(f"{_BASE}/activities/ttl")

    def purge(self, filters: dict[str, Any] | None = None) -> None:
        """Mass-delete audit records by body filter (``DELETE .../activities``).

        .. warning::
            Destructive and effectively irreversible — deletes audit history
            matching ``filters`` (an unfiltered call may purge everything). The
            server's exact filter semantics are unverified; use with caution.

        Example:
            >>> client = demo_client()
            >>> client.audit.purge(filters={"operation": "login"})
        """
        self.client.delete(f"{_BASE}/activities", data=filters or {})
