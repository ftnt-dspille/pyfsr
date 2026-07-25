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
