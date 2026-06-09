"""Typed models for FortiSOAR's stable, platform-owned *system* entities.

Unlike user-mutable modules (alerts/incidents, which routinely gain custom
fields), these entities have **fixed, platform-owned schemas** — playbooks,
playbook collections, playbook runs, and Content Hub items. That makes them the
highest-value targets for hard typing (see the SDK roadmap §7).

These are **curated by hand** (live-verified against a dev box), not generated
from the OpenAPI spec — the curated spec doesn't carry these schemas. Every
model still subclasses :class:`~pyfsr.models.base.BaseRecord`, so it stays
dict-compatible and tolerates extra/unknown fields (``extra="allow"``).
"""

from __future__ import annotations

from typing import Any

from .base import BaseRecord

# Relationship / embedded-object fields (createUser, modifyUser, priority, ...)
# come back as expanded dicts; keep them ``Any`` so the model never breaks.


class Workflow(BaseRecord):
    """A playbook (workflow) record from ``/api/3/workflows/``.

    Stable platform schema. ``collection`` is the IRI of the owning
    :class:`WorkflowCollection`; ``triggerStep`` the IRI of the start step.
    """

    name: str | None = None
    aliasName: str | None = None
    tag: str | None = None
    description: str | None = None
    isActive: bool | None = None
    debug: bool | None = None
    singleRecordExecution: bool | None = None
    remoteExecutableFlag: bool | None = None
    synchronous: bool | None = None
    triggerLimit: Any | None = None
    parameters: list[Any] | None = None
    lastModifyDate: int | None = None
    collection: str | None = None
    triggerStep: str | None = None
    priority: Any | None = None
    playbookOrigin: Any | None = None
    isEditable: bool | None = None
    isPrivate: bool | None = None
    createUser: Any | None = None
    createDate: float | None = None
    modifyUser: Any | None = None
    modifyDate: float | None = None
    deletedAt: Any | None = None
    importedBy: list[Any] | None = None
    recordTags: list[Any] | None = None
    id: int | None = None


class WorkflowCollection(BaseRecord):
    """A playbook collection from ``/api/3/workflow_collections/``.

    The folder that groups playbooks; stable platform schema.
    """

    name: str | None = None
    description: str | None = None
    visible: bool | None = None
    image: Any | None = None
    createUser: Any | None = None
    createDate: float | None = None
    modifyUser: Any | None = None
    modifyDate: float | None = None
    deletedAt: Any | None = None
    importedBy: list[Any] | None = None
    recordTags: list[Any] | None = None
    id: int | None = None


class WorkflowRun(BaseRecord):
    """A playbook *run* record from ``/api/wf/api/(historical-)workflows/``.

    The raw run entity. ``PlaybooksAPI`` also exposes a flattened shape via its
    default (dict) return; pass ``typed=True`` there to get this model instead.
    """

    name: str | None = None
    status: str | None = None
    created: str | None = None
    modified: str | None = None
    parent_wf: Any | None = None
    tags: str | None = None
    debug: bool | None = None
    node_name: str | None = None
    task_id: str | None = None
    result: Any | None = None


class ContentHubItem(BaseRecord):
    """Shared base for Content Hub items (solution packs, connectors, widgets).

    Returned by ``client.content_hub`` searches when ``typed=True``. Stable,
    platform-owned schema (the marketplace catalog shape).
    """

    name: str | None = None
    label: str | None = None
    description: str | None = None
    type: str | None = None
    version: str | None = None
    installed: bool | None = None
    latestAvailableVersion: str | None = None
    latestCompatibleVersion: str | None = None
    fsrMinCompatibility: str | None = None
    publisher: str | None = None
    certified: bool | None = None
    featured: bool | None = None
    featuredTags: list[Any] | None = None
    draft: bool | None = None
    local: bool | None = None
    development: bool | None = None
    dependencies: list[Any] | None = None
    category: list[Any] | None = None
    iconLarge: str | None = None
    publishedDate: int | None = None
    buildNumber: int | None = None
    configCount: int | None = None
    status: Any | None = None
    createUser: Any | None = None
    createDate: float | None = None
    modifyUser: Any | None = None
    modifyDate: float | None = None
    recordTags: list[Any] | None = None
    importedBy: list[Any] | None = None


class SolutionPack(ContentHubItem):
    """A Content Hub **solution pack** (``type == "solutionpack"``)."""


class ContentHubConnector(ContentHubItem):
    """A Content Hub **connector** listing (``type == "connector"``).

    Named ``ContentHubConnector`` to avoid clashing with the live
    ``client.connectors`` (execution) surface — this is the *catalog* entry.
    """


class Widget(ContentHubItem):
    """A Content Hub **widget** (``type == "widget"``)."""
