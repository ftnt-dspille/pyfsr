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

from pydantic import Field

from ._integration import ApiResult
from .base import BaseRecord
from .types import PicklistIRI

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
    parameters: list[str] | None = None  # declared input names, e.g. ["ip4AddressList"] (live-verified)
    lastModifyDate: int | None = None
    collection: str | None = None
    triggerStep: str | None = None
    priority: PicklistIRI | None = None
    playbookOrigin: PicklistIRI | None = None
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


class FeaturedTag(ApiResult):
    """A marketplace "featured" badge on a Content Hub item.

    The ``featuredTags`` array on a :class:`ContentHubItem` carries these —
    live-verified shape is ``{"tag": "preview", "color": "#2d87e3"}`` (the label
    and the hex colour the catalog UI renders the chip with). Dict-compatible,
    so ``tag["tag"]`` works alongside ``tag.tag``.
    """

    tag: str | None = None
    color: str | None = None


class ContentHubItem(BaseRecord):
    """Shared base for Content Hub items (solution packs, connectors, widgets).

    Returned by ``client.content_hub`` searches. Stable, platform-owned schema
    (the marketplace catalog shape). Subclassed by :class:`SolutionPack`,
    :class:`ContentHubConnector`, and :class:`Widget`, which add nothing of their
    own today — the catalog returns one flat shape discriminated by ``type`` —
    but exist so callers can ``isinstance``-narrow and so future per-type fields
    have a home.
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
    featuredTags: list[FeaturedTag] | None = None
    draft: bool | None = None
    local: bool | None = None
    development: bool | None = None
    dependencies: list[Any] | None = None
    category: list[Any] | None = None
    iconLarge: str | None = None
    infoPath: str | None = None  # repo path to the item's info.json (drives connector_versions)
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


class ModulePermission(BaseRecord):
    """One module's CRUD/execute grant inside a :class:`Role`.

    Live-verified shape (``@type == "ModulePermission"``): the five ``can*``
    booleans, an optional ``fieldPermissions`` list, and a ``module``
    relationship (an IRI string, or the expanded module object when relationships
    are pulled). Dict-compatible, so ``perm["canRead"]`` works alongside
    ``perm.canRead``.
    """

    canCreate: bool | None = None
    canRead: bool | None = None
    canUpdate: bool | None = None
    canDelete: bool | None = None
    canExecute: bool | None = None
    fieldPermissions: list[Any] | None = None
    module: Any | None = None  # IRI str or expanded module dict per $relationships


class Role(BaseRecord):
    """A FortiSOAR **role** record from ``/api/3/roles/``.

    A role bundles module permissions and is assigned to users. The module slug
    is ``roles``; ``@type`` on the wire is ``Role``. ``modulePermissions`` is
    only populated when the record is fetched with ``$relationships=true``
    (verified against a live 7.6.5 box).
    """

    name: str | None = None
    description: str | None = None
    modulePermissions: list[ModulePermission] | None = None
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


class ImportJob(ApiResult):
    """The async import job embedded in a solution-pack install response.

    ``POST /api/3/solutionpacks/install`` returns the pack entity with this
    object tracking the install; its ``uuid`` is what
    :meth:`~pyfsr.api.solution_packs.SolutionPackAPI.install_status` and
    :meth:`~pyfsr.api.solution_packs.SolutionPackAPI.wait_for_install` poll.
    Dict-compatible, so ``job["uuid"]`` works alongside ``job.uuid``.
    """

    id_iri: str | None = Field(default=None, alias="@id")
    uuid: str | None = None
    status: str | None = None


class SolutionPackInstallResponse(SolutionPack):
    """The SolutionPack record returned by ``POST /api/3/solutionpacks/install``.

    The install response is the full SolutionPack entity with an embedded
    :class:`ImportJob` tracking the async install. Use :attr:`job_id` to
    get the UUID for :meth:`~pyfsr.api.solution_packs.SolutionPackAPI.install_status`
    and :meth:`~pyfsr.api.solution_packs.SolutionPackAPI.wait_for_install` calls.
    """

    importJob: ImportJob | None = None

    @property
    def job_id(self) -> str | None:
        """UUID of the async import job, parsed from the embedded :class:`ImportJob`."""
        job = self.importJob
        if job is None:
            return None
        if job.uuid:
            return job.uuid
        if job.id_iri:
            return job.id_iri.rstrip("/").split("/")[-1]
        return None


class ContentHubConnector(ContentHubItem):
    """A Content Hub **connector** listing (``type == "connector"``).

    Named ``ContentHubConnector`` to avoid clashing with the live
    ``client.connectors`` (execution) surface — this is the *catalog* entry.
    """


class Widget(ContentHubItem):
    """A Content Hub **widget** (``type == "widget"``)."""


class ConnectorOperation(ApiResult):
    """One action a connector exposes, from its ``info.json`` ``operations[]``.

    Live-verified stable fields: the ``operation`` slug, human ``title`` /
    ``description``, and the ``visible`` flag. Operation-specific extras
    (parameters, output schema, category, …) stay in ``extra``. Dict-compatible.
    """

    operation: str | None = None
    title: str | None = None
    description: str | None = None
    visible: bool | None = None


class ConnectorVersionInfo(ApiResult):
    """A connector's published ``info.json`` from Fortinet's public Content Hub repo.

    Returned by :meth:`~pyfsr.api.content_hub.ContentHubSearch.connector_versions`.
    This is the *repo* manifest (``{repo}/.../latest/info.json``), a different
    shape from the on-box :class:`ContentHubConnector` catalog entry — most
    notably it carries :attr:`availableVersions`, every version ever published.
    Curated fields are typed; the rest (``scm``, ``help``, icon paths, …) stay in
    ``extra``. Dict-compatible, so ``info["availableVersions"]`` still works.
    """

    name: str | None = None
    label: str | None = None
    description: str | None = None
    version: str | None = None
    type: str | None = None
    buildNumber: int | None = None
    publishedDate: int | None = None
    lastUpdated: int | None = None
    publisher: str | None = None
    certified: bool | None = None
    category: str | None = None
    infoPath: str | None = None
    help: str | None = None
    releaseNotes: str | None = None
    availableVersions: list[str] | None = None
    operations: list[ConnectorOperation] | None = None
    dependentSolutionPacks: list[Any] | None = None
