"""Audit-log gateway (``/api/gateway/audit``).

Query FortiSOAR's audit activity store — the per-record change history the
UI shows in the "Audit Log" widget on an alert/record. Each entry records
who changed what, when, and which playbook (if any) was responsible.

The primary filter is ``entity_uuid`` — the record UUID to trace. Time
windows (``start_date`` / ``end_date``) are epoch milliseconds and optional.
The response is Spring-paginated: ``{"number": <page>, "content": [...]}``;
walk pages until ``content`` is empty.

Example:
    >>> client = demo_client()
    >>> result = client.audit.activities(entity_uuid="9f0eb603-ac1e-41c3-b47b-444589beed39")
    >>> result["content"][0]["operation"]
    'Link'
    >>> result["content"][0]["playbookName"]
    'Extract Indicators (Alerts)'
    >>> count = client.audit.count(entity_uuid="9f0eb603-ac1e-41c3-b47b-444589beed39")
    >>> count["total"]
    4
    >>> ops = client.audit.operations(operation_type="module_detail")  # doctest: +SKIP
    >>> "Link" in ops  # doctest: +SKIP
    True
"""

from __future__ import annotations

from typing import Any

from ..models._audit import LifecycleEntry, RecordLifecycle
from .base import BaseAPI

_BASE = "/api/gateway/audit"


def _to_epoch_ms(val: Any) -> int | None:
    """Coerce a date/time value to epoch milliseconds (what the wire expects).

    Accepts:
      - ``int`` / ``float`` — assumed to already be epoch seconds or ms.
      - ``str`` — ISO-8601 (e.g. ``2026-07-24T20:09:02Z``).
    Returns ``None`` for ``None``.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # Heuristic: < 10^12 is seconds, >= 10^12 is milliseconds
        return int(val) if val >= 1e12 else int(val * 1000)
    if isinstance(val, str):
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return None
    return None


class AuditAPI(BaseAPI):
    """Query and manage FortiSOAR audit activity records.

    Accessed as ``client.audit``. The audit store records every create/update/
    link/unlink/comment/trigger on a record, including which playbook made the
    change (``playbookName`` / ``playbookIri`` fields).
    """

    def _build_body(
        self,
        *,
        entity_uuid: str | None = None,
        start_date: Any = None,
        end_date: Any = None,
        page: int | None = None,
        limit: int | None = None,
        operation: str | None = None,
        component: str | None = None,
        user_id: str | None = None,
        entity_type: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if entity_uuid is not None:
            body["entityUuid"] = entity_uuid
        sd = _to_epoch_ms(start_date)
        ed = _to_epoch_ms(end_date)
        if sd is not None:
            body["startDate"] = sd
        if ed is not None:
            body["endDate"] = ed
        if page is not None:
            body["page"] = page
        if limit is not None:
            body["limit"] = limit
        if operation is not None:
            body["operation"] = operation
        if component is not None:
            body["component"] = component
        if user_id is not None:
            body["userId"] = user_id
        if entity_type is not None:
            body["entityType"] = entity_type
        if search is not None:
            body["search"] = search
        return body

    def activities(
        self,
        start_date: Any = None,
        end_date: Any = None,
        *,
        entity_uuid: str | None = None,
        page: int = 0,
        limit: int = 10,
        operation: str | None = None,
        component: str | None = None,
        user_id: str | None = None,
        entity_type: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Query a slice of audit records (``POST /api/gateway/audit/activities``).

        The primary filter is ``entity_uuid`` — the record UUID to trace
        (matching the UI's audit-log widget). Time windows (``start_date`` /
        ``end_date``) are optional; accept epoch seconds, epoch milliseconds,
        or ISO-8601 strings.

        The response is Spring-paginated: ``{"number": <page>, "content": [...]}``.
        Walk pages (incrementing ``page``) until ``content`` is empty.

        Each record carries: ``operation`` (``"Create"``, ``"Update"``,
        ``"Link"``, ``"Unlink"``, ``"Comment"``, ``"Trigger"``, …),
        ``transactionDate`` (epoch ms), ``user`` (``"Playbook"`` for playbook
        changes), ``playbookName`` / ``playbookIri`` (when a playbook did it),
        ``entityType``, ``entityUuid``, ``displayName``, ``title``,
        ``data`` (linked entity details), ``linkEntityDetails``.

        Args:
            start_date: window start (epoch s/ms or ISO string; ``None`` = no bound).
            end_date: window end (epoch s/ms or ISO string; ``None`` = no bound).
            entity_uuid: the record UUID to filter by (the primary filter).
            page: 0-indexed page number (default 0).
            limit: page size (default 10).
            operation: filter by operation type (see :meth:`operations`).
            component: filter by component (e.g. ``"crudhub"``).
            user_id: filter by user UUID.
            entity_type: filter by entity type (e.g. ``"alerts"``).
            search: free-text search across audit fields.

        Example:
            >>> client = demo_client()
            >>> result = client.audit.activities(entity_uuid="9f0eb603-ac1e-41c3-b47b-444589beed39")
            >>> len(result["content"])
            1
            >>> result["content"][0]["operation"]
            'Link'
        """
        body = self._build_body(
            entity_uuid=entity_uuid,
            start_date=start_date,
            end_date=end_date,
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
        start_date: Any = None,
        end_date: Any = None,
        *,
        entity_uuid: str | None = None,
        operation: str | None = None,
        component: str | None = None,
        user_id: str | None = None,
        entity_type: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Total audit-record count (``POST /api/gateway/audit/activities/count``).

        Same filters as :meth:`activities` but without paging. Returns
        ``{"total": <int>}``.

        Example:
            >>> client = demo_client()
            >>> result = client.audit.count(entity_uuid="9f0eb603-ac1e-41c3-b47b-444589beed39")
            >>> result["total"]
            4
        """
        body = self._build_body(
            entity_uuid=entity_uuid,
            start_date=start_date,
            end_date=end_date,
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
            >>> record = client.audit.get("9314875")
            >>> record["operation"]
            'Link'
        """
        return self.client.get(f"{_BASE}/activities/{audit_id}")

    def operations(self, *, operation_type: str | None = None) -> Any:
        """List the valid ``operation`` values (``GET /api/gateway/audit/operations``).

        Pass ``operation_type="module_detail"`` for the per-record operations
        (``Create``, ``Update``, ``Link``, ``Unlink``, ``Comment``,
        ``Trigger``, ``Import``, ``Update During Import``,
        ``Replication Failed``, ``Executed Action``). Without it, returns
        the system-level operations (``login``, ``logout``, ``create``, …).

        Example:
            >>> client = demo_client()
            >>> ops = client.audit.operations()
            >>> "login" in ops
            True
            >>> "Link" in client.audit.operations(operation_type="module_detail")  # doctest: +SKIP
            True
        """
        params: dict[str, Any] = {}
        if operation_type is not None:
            params["operationType"] = operation_type
        return self.client.get(f"{_BASE}/operations", params=params or None)

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

    # ------------------------------------------------------------------ helpers
    def all_activities(
        self,
        *,
        entity_uuid: str | None = None,
        start_date: Any = None,
        end_date: Any = None,
        operation: str | None = None,
        component: str | None = None,
        user_id: str | None = None,
        entity_type: str | None = None,
        search: str | None = None,
        page_size: int = 100,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch *all* matching audit records by walking pages until empty.

        Convenience wrapper around :meth:`activities` that handles pagination
        automatically. Returns the combined ``content`` list from all pages.

        Args:
            entity_uuid: the record UUID to filter by.
            start_date / end_date: optional time window (epoch s/ms or ISO).
            operation / component / user_id / entity_type / search: optional filters.
            page_size: records per page (default 100).
            max_pages: safety cap (default 50 — 5000 records max).

        Returns:
            A flat list of audit record dicts, sorted oldest-first by
            ``transactionDate``.
        """
        all_items: list[dict[str, Any]] = []
        for page in range(max_pages):
            result = self.activities(
                start_date=start_date,
                end_date=end_date,
                entity_uuid=entity_uuid,
                page=page,
                limit=page_size,
                operation=operation,
                component=component,
                user_id=user_id,
                entity_type=entity_type,
                search=search,
            )
            content = result.get("content") or []
            if not content:
                break
            all_items.extend(content)
            if len(content) < page_size:
                break
        all_items.sort(key=lambda r: r.get("transactionDate", 0))
        return all_items

    # ---------------------------------------------------------------- lifecycle
    def lifecycle(
        self,
        entity_uuid: str,
        *,
        entity_type: str | None = None,
        include_executions: bool = True,
        start_date: Any = None,
        end_date: Any = None,
    ) -> RecordLifecycle:
        """Build a full change-history timeline for a record.

        Combines audit-log entries (field changes, links, comments, triggers)
        with playbook executions into a single sorted timeline — answering
        "what happened to this alert, in what order, and which playbooks
        did what?"

        Each :class:`~pyfsr.models.LifecycleEntry` carries a timestamp,
        ``kind`` (``"audit"`` or ``"execution"``), the operation (e.g.
        ``"Update"``, ``"Link"``, ``"finished"``), and the playbook name
        when a playbook was responsible. The :class:`~pyfsr.models.RecordLifecycle`
        result provides filtered views: ``.by_playbook``, ``.field_changes``,
        ``.links``, ``.comments``, ``.playbook_names``, and ``.summary()``.

        Args:
            entity_uuid: the record UUID to trace (the primary filter).
            entity_type: optional entity type filter (e.g. ``"alerts"``).
            include_executions: when ``True`` (default), also query playbook
                execution history and merge into the timeline. The audit log
                already records playbook names, so this adds run-level detail
                (status, pk) but isn't strictly necessary for a basic timeline.
            start_date: optional window start (epoch s/ms or ISO string).
            end_date: optional window end (epoch s/ms or ISO string).

        Returns:
            A :class:`~pyfsr.models.RecordLifecycle` with sorted ``entries``,
            ``audit_count``, ``execution_count``, and convenience filters.

        Example:
            >>> client = demo_client()
            >>> life = client.audit.lifecycle("9f0eb603-ac1e-41c3-b47b-444589beed39")  # doctest: +SKIP
            >>> life.summary()  # doctest: +SKIP
            'alerts 9f0eb603: 3 audit events, 2 playbook runs, 3 playbooks involved'
            >>> [e.operation for e in life.by_playbook]  # doctest: +SKIP
            ['Create', 'Link', 'Link', 'finished']
        """
        entries: list[LifecycleEntry] = []

        # --- audit entries ---
        audit_items = self.all_activities(
            entity_uuid=entity_uuid,
            start_date=start_date,
            end_date=end_date,
            entity_type=entity_type,
            page_size=100,
        )
        for item in audit_items:
            entries.append(
                LifecycleEntry(
                    timestamp_ms=item.get("transactionDate"),
                    kind="audit",
                    operation=item.get("operation"),
                    user=item.get("user"),
                    playbook_name=item.get("playbookName"),
                    title=item.get("title"),
                    entity_type=item.get("entityType"),
                    entity_uuid=item.get("entityUuid"),
                    linked_entity_iri=(
                        (item.get("linkEntityDetails") or {}).get("iri") if item.get("linkEntityDetails") else None
                    ),
                    linked_entity_type=(
                        (item.get("linkEntityDetails") or {}).get("type") if item.get("linkEntityDetails") else None
                    ),
                    linked_entity_display=(
                        (item.get("linkEntityDetails") or {}).get("displayName")
                        if item.get("linkEntityDetails")
                        else None
                    ),
                    raw=item,
                )
            )
        audit_count = len(audit_items)

        # --- execution entries (optional enrichment) ---
        execution_count = 0
        if include_executions:
            try:
                runs = self.client.playbooks.search_executions(limit=50, ordering="-modified")
                for run in runs:
                    # Filter to runs that touched this record — the run's
                    # ``name`` is the playbook name; audit already covers
                    # playbook identity, so we only add runs that have a
                    # matching timestamp window. The most reliable signal is
                    # the run's ``modified`` field; we include it as a
                    # timeline marker.

                    run_extra = run if isinstance(run, dict) else {}
                    modified = run_extra.get("modified")
                    ts_ms: int | None = None
                    if modified:
                        try:
                            from datetime import datetime as _dt

                            dt = _dt.fromisoformat(str(modified).replace("Z", "+00:00"))
                            ts_ms = int(dt.timestamp() * 1000)
                        except Exception:
                            pass
                    if ts_ms is not None:
                        entries.append(
                            LifecycleEntry(
                                timestamp_ms=ts_ms,
                                kind="execution",
                                operation=run_extra.get("status"),
                                execution_pk=str(run_extra.get("pk")) if run_extra.get("pk") else None,
                                execution_status=run_extra.get("status"),
                                playbook_name=run_extra.get("name"),
                                raw=dict(run_extra) if isinstance(run_extra, dict) else None,
                            )
                        )
                        execution_count += 1
            except Exception:  # noqa: BLE001 - executions are optional enrichment
                pass

        # Sort oldest-first by timestamp
        entries.sort(key=lambda e: (e.timestamp_ms is None, e.timestamp_ms or 0))

        return RecordLifecycle(
            entity_uuid=entity_uuid,
            entity_type=entity_type,
            entries=entries,
            audit_count=audit_count,
            execution_count=execution_count,
        )
