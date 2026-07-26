"""Typed models for the audit-log gateway (``client.audit``).

The :class:`AuditActivity` wraps a single audit record (create/update/link/
comment/trigger) with typed accessors for the key fields an agent needs to
build a change timeline.
"""

from __future__ import annotations

import time
from typing import Any

from ._integration import ApiResult


class AuditActivity(ApiResult):
    """One audit-log entry — a single change event on a record.

    Carries: ``operation`` (Create/Update/Link/Unlink/Comment/Trigger/…),
    ``transaction_date`` (epoch ms), ``user`` (``"Playbook"`` for playbook
    changes), ``playbook_name`` / ``playbook_iri`` (when a playbook did it),
    ``entity_type``, ``entity_uuid``, ``title``, and ``data`` (linked entity
    details, old/new values, etc.).
    """

    operation: str | None = None
    transaction_date: int | None = None
    user: str | None = None
    user_id: str | None = None
    playbook_name: str | None = None
    playbook_iri: str | None = None
    entity_type: str | None = None
    entity_uuid: str | None = None
    display_name: str | None = None
    title: str | None = None
    component: str | None = None
    source: str | None = None
    data: dict[str, Any] | None = None
    link_entity_details: dict[str, Any] | None = None
    id: int | str | None = None

    @property
    def timestamp_iso(self) -> str | None:
        """``transaction_date`` as an ISO-8601 string, or ``None``."""
        if self.transaction_date is None:
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.transaction_date / 1000))

    @property
    def by_playbook(self) -> bool:
        """``True`` when a playbook made this change (``user == "Playbook"``)."""
        return (self.user or "").lower() == "playbook" and bool(self.playbook_name)

    @property
    def linked_entity_iri(self) -> str | None:
        """The IRI of the entity linked/unlinked (``linkEntityDetails.iri``), or ``None``."""
        if self.link_entity_details and isinstance(self.link_entity_details, dict):
            return self.link_entity_details.get("iri")
        return None

    @property
    def linked_entity_type(self) -> str | None:
        """The type of the linked entity (``indicators``, ``assets``…), or ``None``."""
        if self.link_entity_details and isinstance(self.link_entity_details, dict):
            return self.link_entity_details.get("type")
        return None

    @property
    def linked_entity_display(self) -> str | None:
        """The display name of the linked entity, or ``None``."""
        if self.link_entity_details and isinstance(self.link_entity_details, dict):
            return self.link_entity_details.get("displayName")
        return None


class LifecycleEntry(ApiResult):
    """One entry in a record's lifecycle timeline (from :meth:`~pyfsr.api.audit.AuditAPI.lifecycle`).

    A unified view of either an audit-log change or a playbook execution,
    sorted by timestamp. ``kind`` distinguishes the source: ``"audit"`` for
    a field change / link / comment, ``"execution"`` for a playbook run.
    """

    timestamp_ms: int | None = None
    kind: str | None = None  # "audit" or "execution"
    operation: str | None = None  # audit operation or execution status
    user: str | None = None  # "Playbook", "CS Admin", etc.
    playbook_name: str | None = None
    title: str | None = None
    entity_type: str | None = None
    entity_uuid: str | None = None
    linked_entity_iri: str | None = None
    linked_entity_type: str | None = None
    linked_entity_display: str | None = None
    execution_pk: str | None = None
    execution_status: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def timestamp_iso(self) -> str | None:
        """Timestamp as an ISO-8601 string, or ``None``."""
        if self.timestamp_ms is None:
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.timestamp_ms / 1000))


class RecordLifecycle(ApiResult):
    """The full change-history timeline for a record (from :meth:`~pyfsr.api.audit.AuditAPI.lifecycle`).

    Combines audit-log entries (field changes, links, comments) with playbook
    executions into a single sorted timeline. ``entries`` is oldest-first;
    ``by_playbook`` / ``state_changes`` provide filtered views.
    """

    entity_uuid: str | None = None
    entity_type: str | None = None
    entries: list[LifecycleEntry] = []
    audit_count: int = 0
    execution_count: int = 0

    @property
    def by_playbook(self) -> list[LifecycleEntry]:
        """Entries caused by a playbook (``user == "Playbook"`` or kind == ``"execution"``)."""
        return [e for e in self.entries if e.user and e.user.lower() == "playbook" or e.kind == "execution"]

    @property
    def field_changes(self) -> list[LifecycleEntry]:
        """Audit entries with operation ``Update`` (field-level changes)."""
        return [e for e in self.entries if e.operation == "Update"]

    @property
    def links(self) -> list[LifecycleEntry]:
        """Audit entries with operation ``Link`` or ``Unlink``."""
        return [e for e in self.entries if e.operation in ("Link", "Unlink")]

    @property
    def comments(self) -> list[LifecycleEntry]:
        """Audit entries with operation ``Comment``."""
        return [e for e in self.entries if e.operation == "Comment"]

    @property
    def playbook_names(self) -> list[str]:
        """Distinct playbook names that touched this record."""
        seen: list[str] = []
        for e in self.entries:
            if e.playbook_name and e.playbook_name not in seen:
                seen.append(e.playbook_name)
        return seen

    def summary(self) -> str:
        """A one-line summary suitable for agent output."""
        return (
            f"{self.entity_type or 'record'} {self.entity_uuid[:8] if self.entity_uuid else '?'}: "
            f"{self.audit_count} audit events, {self.execution_count} playbook runs, "
            f"{len(self.playbook_names)} playbooks involved"
        )


class ExecutionContext(ApiResult):
    """What was happening to a record around the time of a specific playbook run.

    Returned by :meth:`client.audit.execution_context
    <pyfsr.api.audit.AuditAPI.execution_context>`. Answers the debugging
    question "why did this playbook see state X when I expected state Y?" by
    showing what *other* playbooks or manual actions changed the record
    within the run's time window.

    ``concurrent_changes`` are audit events on the same record that happened
    during the run (between its ``created`` and ``modified`` timestamps, ± a
    buffer). ``concurrent_runs`` are other playbook executions on the same
    record in the same window. ``before_changes`` are audit events just before
    the run started (context for what state the playbook saw).
    """

    run_pk: str | None = None
    run_name: str | None = None
    run_status: str | None = None
    run_created: str | None = None
    run_modified: str | None = None
    record_uuid: str | None = None
    record_iri: str | None = None
    entity_type: str | None = None
    concurrent_changes: list[LifecycleEntry] = []
    concurrent_runs: list[dict[str, Any]] = []
    before_changes: list[LifecycleEntry] = []
    window_seconds: int = 60

    @property
    def other_playbooks(self) -> list[str]:
        """Playbook names (excluding the run itself) that changed the record."""
        seen: list[str] = []
        for e in self.concurrent_changes:
            if e.playbook_name and e.playbook_name != self.run_name and e.playbook_name not in seen:
                seen.append(e.playbook_name)
        for r in self.concurrent_runs:
            name = r.get("name")
            if name and name != self.run_name and name not in seen:
                seen.append(name)
        return seen

    def summary(self) -> str:
        """A one-line summary suitable for agent output."""
        other = self.other_playbooks
        parts = [
            f"run {self.run_name!r} (pk={self.run_pk}) {self.run_status}",
            f"on {self.entity_type or 'record'} {self.record_uuid[:8] if self.record_uuid else '?'}",
            f"{len(self.concurrent_changes)} concurrent changes",
            f"{len(self.concurrent_runs)} concurrent runs",
        ]
        if other:
            parts.append(f"other playbooks: {', '.join(other)}")
        if self.before_changes:
            parts.append(f"{len(self.before_changes)} prior changes")
        return ", ".join(parts)
