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
    priority: str | None = None  # picklist IRI
    playbookOrigin: str | None = None  # picklist IRI
    isEditable: bool | None = None
    isPrivate: bool | None = None
    createUser: str | dict[str, Any] | None = None
    createDate: float | None = None
    modifyUser: str | dict[str, Any] | None = None
    modifyDate: float | None = None
    deletedAt: float | None = None  # soft-delete epoch (like create/modifyDate); null until deleted
    importedBy: list[Any] | None = None
    recordTags: list[str] | None = None  # tag name strings
    id: int | None = None


class WorkflowCollection(BaseRecord):
    """A playbook collection from ``/api/3/workflow_collections/``.

    The folder that groups playbooks; stable platform schema.
    """

    name: str | None = None
    description: str | None = None
    visible: bool | None = None
    image: Any | None = None
    createUser: str | dict[str, Any] | None = None
    createDate: float | None = None
    modifyUser: str | dict[str, Any] | None = None
    modifyDate: float | None = None
    deletedAt: float | None = None  # soft-delete epoch (like create/modifyDate); null until deleted
    importedBy: list[Any] | None = None
    recordTags: list[str] | None = None  # tag name strings
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
    publishedDate: float | None = None
    buildNumber: int | None = None
    configCount: int | None = None
    status: str | None = None  # plain string e.g. "Completed"
    createUser: str | dict[str, Any] | None = None
    createDate: float | None = None
    modifyUser: str | dict[str, Any] | None = None
    modifyDate: float | None = None
    recordTags: Any | None = None
    importedBy: list[Any] | None = None


class Appliance(BaseRecord):
    """A FortiSOAR **appliance** actor from ``/api/3/appliances/``.

    Appears as ``createUser`` / ``modifyUser`` on records created by the
    playbook engine itself (``@type == "Appliance"``). Distinct from a human
    :class:`User` (``@type == "Person"``). ``name`` is typically ``"Playbook"``.
    """

    name: str | None = None
    userType: Any | None = None
    avatar: Any | None = None
    userId: str | None = None
    createUser: str | dict[str, Any] | None = None
    createDate: float | None = None
    modifyUser: str | dict[str, Any] | None = None
    modifyDate: float | None = None
    id: int | None = None


class User(BaseRecord):
    """A FortiSOAR **user** (``Person``) record from ``/api/3/people/``.

    This is the entity behind every ``createUser`` / ``modifyUser`` /
    ``assignedTo`` relationship: when a record is pulled with relationships
    expanded those fields arrive as a full Person object, and
    :meth:`BaseRecord.create_user` / :meth:`~BaseRecord.modify_user` /
    :meth:`~BaseRecord.assigned_to` parse them into this model. ``@type`` on the
    wire is ``Person``; the module slug is ``people``.
    """

    firstname: str | None = None
    lastname: str | None = None
    title: str | None = None
    email: str | None = None
    department: str | None = None
    description: str | None = None
    phoneWork: str | None = None
    phoneMobile: str | None = None
    phoneHome: str | None = None
    phoneFax: str | None = None
    csActive: bool | None = None
    accessType: str | None = None  # license seat: "Named" / "Concurrent" (live-verified str)
    userType: Any | None = None  # not in the people module schema; null on observed boxes
    type: Any | None = None  # picklist relationship (IRI str or expanded dict per $relationships)
    avatar: Any | None = None  # not in the people module schema; null on observed boxes
    companyId: Any | None = None  # 'companies' single-relationship (IRI str or expanded dict)
    userId: str | None = None
    createUser: str | dict[str, Any] | None = None
    createDate: float | None = None
    modifyUser: str | dict[str, Any] | None = None
    modifyDate: float | None = None
    id: int | None = None

    @property
    def name(self) -> str | None:
        """Display name (``"firstname lastname"``), or ``None`` if neither is set."""
        parts = [p for p in (self.firstname, self.lastname) if p]
        return " ".join(parts) or None


class Team(BaseRecord):
    """A FortiSOAR **team** record from ``/api/3/teams/``.

    Teams own records (the ``owners`` relationship) and scope visibility. The
    module slug is ``teams``; ``@type`` on the wire is ``Team``. The schema is
    deliberately slim — verified against a live 7.6.5 box, a team record carries
    only ``name``/``description``/``importedBy`` beyond the JSON-LD/uuid envelope.
    """

    name: str | None = None
    description: str | None = None
    importedBy: list[Any] | None = None


class Role(BaseRecord):
    """A FortiSOAR **role** record from ``/api/3/roles/``.

    A role bundles module permissions and is assigned to users. The module slug
    is ``roles``; ``@type`` on the wire is ``Role``. ``modulePermissions`` is
    only populated when the record is fetched with ``$relationships=true``
    (verified against a live 7.6.5 box).
    """

    name: str | None = None
    description: str | None = None
    modulePermissions: list[Any] | None = None
    importedBy: list[Any] | None = None


class FileRecord(BaseRecord):
    """A ``/api/3/files`` record, returned by :meth:`FileOperations.upload`.

    Stable platform schema. The ``@id`` IRI (``rec.iri``) is what attachment,
    import, and similar payloads reference as their ``file`` field. ``filename``
    and ``mimeType`` are the most-used typed fields; the rest of the storage
    metadata (size, content path, thumbnails) stays in ``extra``.
    """

    filename: str | None = None
    mimeType: str | None = None
    size: int | None = None
    file: Any | None = None
    metadata: Any | None = None
    createUser: str | dict[str, Any] | None = None
    createDate: float | None = None
    modifyUser: str | dict[str, Any] | None = None
    modifyDate: float | None = None
    id: int | str | None = None


class SolutionPack(ContentHubItem):
    """A Content Hub **solution pack** (``type == "solutionpack"``)."""


class SolutionPackInstallResponse(SolutionPack):
    """The SolutionPack record returned by ``POST /api/3/solutionpacks/install``.

    The install response is the full SolutionPack entity with an embedded
    ``importJob`` object tracking the async install. Use :attr:`job_id` to
    get the UUID for :meth:`~pyfsr.api.solution_packs.SolutionPackAPI.install_status`
    and :meth:`~pyfsr.api.solution_packs.SolutionPackAPI.wait_for_install` calls.
    """

    importJob: Any | None = None

    @property
    def job_id(self) -> str | None:
        """UUID of the async import job, parsed from the embedded ``importJob``."""
        job = self.importJob
        if not isinstance(job, dict):
            return None
        uuid = job.get("uuid")
        if isinstance(uuid, str) and uuid:
            return uuid
        iri = job.get("@id")
        if isinstance(iri, str) and iri:
            return iri.rstrip("/").split("/")[-1]
        return None


class ContentHubConnector(ContentHubItem):
    """A Content Hub **connector** listing (``type == "connector"``).

    Named ``ContentHubConnector`` to avoid clashing with the live
    ``client.connectors`` (execution) surface — this is the *catalog* entry.
    """


class Widget(ContentHubItem):
    """A Content Hub **widget** (``type == "widget"``)."""
