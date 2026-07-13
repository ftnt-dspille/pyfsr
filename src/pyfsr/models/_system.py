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

from pydantic import Field, field_validator

from ._integration import ApiResult
from .base import BaseRecord
from .types import PicklistIRI


def _empty_to_none(value: Any) -> Any:
    """Coerce FortiSOAR's empty-object placeholders (``[]``, ``""``, ``{}``) to None.

    The platform returns an empty list or empty string for an *unset* object or
    reference field; this lets such fields be typed as their real model instead of
    a ``... | list[Any]`` union.
    """
    if value == [] or value == "" or value == {}:
        return None
    return value


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
    workflows: list[Any] | None = None  # nested Workflow objects when relationships are pulled
    id: int | None = None


class ReusableBlock(BaseRecord):
    """A **reusable playbook block** — a ``workflow_groups`` row with ``reusable=true``.

    The saved, re-droppable step group surfaced in the playbook editor and the
    Configuration Export wizard's *Playbook Blocks* category. From
    ``GET /api/3/workflow_groups?reusable=true`` (live-verified 8.0).
    """

    name: str | None = None
    description: str | None = None
    type: str | None = None
    reusable: bool | None = None
    hasTriggerStep: bool | None = None
    hideInLogs: bool | None = None
    recordTags: list[str] | None = None  # tag name strings (matches Workflow/Collection)
    #: Editor canvas metadata; a bare ``[]`` when unset (live-verified 8.0).
    metadata: dict[str, Any] | list[Any] | None = None
    # Editor-canvas geometry (``top``/``left``/``width``/``height``/``isCollapsed``)
    # also rides the wire; it's positioning noise, left in ``extra`` untyped.


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
    # Extra fields the historical-workflows endpoint carries (absent on the live
    # /api/wf/api/workflows/ active-run listing; live-verified 8.0.0):
    template_iri: str | None = None  # IRI of the source Workflow (/api/3/workflows/<uuid>)
    user: Any | None = None  # actor that ran it (IRI or expanded dict)
    steps: Any | None = None  # per-step execution records
    env: Any | None = None  # run environment/input snapshot
    metadata: Any | None = None
    peer_details: Any | None = None


class FeaturedTag(ApiResult):
    """A marketplace "featured" badge on a Content Hub item.

    The ``featuredTags`` array on a :class:`ContentHubItem` carries these —
    live-verified shape is ``{"tag": "preview", "color": "#2d87e3"}`` (the label
    and the hex colour the catalog UI renders the chip with). Dict-compatible,
    so ``tag["tag"]`` works alongside ``tag.tag``.
    """

    tag: str | None = None
    color: str | None = None


class AggregateRow(ApiResult):
    """One row of a server-side aggregation.

    Returned by :meth:`~pyfsr.records.RecordSet.aggregate`. The keys are the
    aliases supplied to that call — group-by fields keep the field's last path
    segment, metrics use their explicit alias, and ``count=True`` adds
    ``total`` — so the shape is entirely caller-defined and every key lives in
    ``extra``. Dict-compatible (``row["total"]`` works alongside
    :meth:`value`); :meth:`value` is just a typed accessor for one alias.

    Example::

        rows = client.records("workflows").aggregate(
            group_by="triggerStep.stepType.name", count=True)
        rows[0]["name"], rows[0].value("total")
    """

    def value(self, alias: str, default: Any = None) -> Any:
        """The value stored under ``alias`` (a group-by segment or metric alias)."""
        return self.get(alias, default)


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
    """A FortiSOAR **appliance** actor (``@type == "Appliance"``).

    One of the concrete subtypes of an *actor*: FortiSOAR stores all security
    principals in a single ``actors`` table using single-table inheritance keyed
    on the ``record_type`` discriminator (root-verified against the appliance's
    Doctrine entities — ``Person`` extends ``Actor``, same ``actors`` table). An
    ``Appliance`` is the ``record_type == "Appliance"`` sibling of a human
    :class:`User` (``record_type == "Person"``); it appears as ``createUser`` /
    ``modifyUser`` on records created by the playbook engine itself, where
    ``name`` is typically ``"Playbook"``. The ``/api/3/appliances/`` collection
    is the filtered view of these rows.
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

    A ``Person`` is the human subtype of an *actor*. FortiSOAR keeps every
    security principal in one ``actors`` table via single-table inheritance keyed
    on the ``record_type`` discriminator (root-verified: ``Person`` extends the
    base ``Actor`` entity, same ``actors`` table); the sibling subtypes are
    :class:`Appliance` (``record_type == "Appliance"``) and the :class:`ApiKey`
    actor (``record_type == "ApiKey"``). ``@type`` on the wire is ``Person`` and the module
    slug is ``people`` — the ``/api/3/people`` collection is the person-only view
    of the shared table, whereas ``/api/3/actors`` spans all subtypes.

    This is the entity behind every ``createUser`` / ``modifyUser`` /
    ``assignedTo`` relationship: when a record is pulled with relationships
    expanded those fields arrive as a full Person object, and
    :meth:`BaseRecord.create_user` / :meth:`~BaseRecord.modify_user` /
    :meth:`~BaseRecord.assigned_to` parse them into this model (dispatching to
    :class:`Appliance` when the expanded ``@type`` is ``Appliance``).
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
    # Team membership is over actors (the polymorphic principal), not just people.
    # Expanded rows ($relationships=true) carry an @type and can be any actor
    # subtype: live-verified on 8.0.0 the built-in "SOC Team" holds both Person
    # actors and Appliance actors (the "Playbook"/"Access Node" engine appliances),
    # so this parses as the full Actor union, falling back to the IRI string.
    actors: list[Actor | str] | None = None
    parents: list[Any] | None = None
    siblings: list[Any] | None = None
    children: list[Any] | None = None


class EmailTemplate(BaseRecord):
    """A FortiSOAR **email template** record from ``/api/3/email_templates/``.

    Reusable subject/body used by notification playbooks and the SMTP connector's
    "Email Template" body type. The module slug is ``email_templates``; ``@type``
    on the wire is ``EmailTemplate``. ``subject`` and ``content`` may contain
    Jinja that the platform expands at send time (verified against a live 8.0 box).
    """

    name: str | None = None
    subject: str | None = None
    content: str | None = None
    visible: bool | None = None


class Notification(BaseRecord):
    """A FortiSOAR **system notification** from ``/api/rule/api/system-notification/notifications/``.

    The per-user bell-icon notifications the platform raises for record events
    (task assignments, approvals, SLA breaches, …). This is a ``rule`` API entity,
    not a ``/api/3`` module, so there is no JSON-LD envelope — ``uuid`` is the
    identity and ``id_iri``/``record_type`` stay ``None``. Field set captured from
    a live 8.0 box.

    ``content`` is the rendered HTML shown in the notification panel; ``entity_type``
    / ``entity_id`` point at the record the event fired on (e.g. ``"tasks"`` plus a
    uuid), and ``event_type`` is the action (``"create"`` / ``"update"`` / …).
    ``read`` / ``dismissible`` drive the panel's unread badge and dismiss control.
    """

    content: str | None = None
    footer: list[Any] | None = None
    entity_type: str | None = None
    event_type: str | None = None
    entity_id: str | None = None
    read: bool | None = None
    dismissible: bool | None = None
    created_on: str | None = None
    roles: list[Any] | None = None
    user: str | None = None  # owning person uuid
    teams: list[Any] | None = None


class ManualInputVariable(ApiResult):
    """One field in a Manual Input prompt's collected form (``inputVariables[]``).

    Field set captured from a live ``retrieve_wfinput`` response: a friendly
    ``inputs:`` field compiles to this canonical shape. ``name`` is the variable
    referenced after resume as ``vars.steps.<step>.input.<name>``; ``formType`` /
    ``dataType`` / ``type`` / ``templateUrl`` drive how FortiSOAR renders and
    validates the widget (e.g. ``formType="dynamicList"`` with ``options`` is a
    select; ``required`` gates submission). ``options`` is present only for the
    list widgets. Unknown/internal keys (``_expanded``, ``_previousName``, …)
    ride through via ``extra="allow"``.
    """

    name: str | None = None
    type: str | None = None  # "string" | "array" | "integer" | "object" | <module> (lookup)
    label: str | None = None
    title: str | None = None
    tooltip: str | None = None
    dataType: str | None = None
    formType: str | None = None
    required: bool | None = None
    options: list[Any] | None = None  # static enum values for select/dynamicList kinds
    defaultValue: Any | None = None
    templateUrl: str | None = None
    playbookField: bool | None = None
    jinjaExpressionView: bool | None = None
    useRecordFieldDefault: bool | None = None
    usable: bool | None = None


class ManualInputSchema(ApiResult):
    """The form schema of a Manual Input prompt (``input.schema``).

    Live-verified: ``title`` / ``description`` are the prompt header, and
    ``inputVariables`` the ordered list of fields the user fills in (empty for a
    button-only / DecisionBased prompt).
    """

    title: str | None = None
    description: str | None = None
    inputVariables: list[ManualInputVariable] | None = None


class ManualInputForm(ApiResult):
    """The ``input`` object of a retrieved Manual Input: wraps the form schema.

    The wire key is ``schema``; it is exposed as the ``schema_`` attribute
    (``schema`` shadows ``BaseModel.schema``) but stays reachable by its wire name
    through dict access -- ``form["schema"]`` returns the typed
    :class:`ManualInputSchema`.
    """

    schema_: ManualInputSchema | None = Field(default=None, alias="schema")


class ManualInputOption(ApiResult):
    """One response button of a Manual Input (``response_mapping.options[]``).

    ``option`` is the button label; ``step_iri`` the workflow step the run routes
    to when chosen; ``primary`` marks the default/highlighted button (absent on
    plain buttons). Live-verified from ``retrieve_wfinput``.
    """

    option: str | None = None
    step_iri: str | None = None
    primary: bool | None = None


class ResponseMapping(ApiResult):
    """A Manual Input's response options + post-resume messaging (``response_mapping``).

    Live-verified: ``options`` are the buttons, ``duplicateOption`` the
    allow-duplicate flag, ``customSuccessMessage`` the toast shown on resume.
    """

    options: list[ManualInputOption] | None = None
    duplicateOption: bool | None = None
    customSuccessMessage: str | None = None


class ManualInput(BaseRecord):
    """A pending **manual workflow input** from ``/api/wf/api/manual-wf-input/``.

    A playbook paused on a Manual Input / Approval step, waiting on a human. This
    is a ``wf`` API entity, not a ``/api/3`` module, so there is no JSON-LD
    envelope -- ``id`` (int) is the identity and ``id_iri``/``record_type`` stay
    ``None``. Field set captured from a live 8.0 box.

    ``workflow`` is the encrypted run token (Fernet), ``step_id`` the paused
    step, and ``is_approval`` distinguishes an approval gate from a data-input
    prompt. ``assignment_type`` / ``owners`` / ``owner_details`` describe who the
    input is assigned to.
    """

    id: int | None = None
    record: str | None = None
    type: str | None = None
    title: str | None = None
    external_channel_list: list[Any] | None = None
    inline_channel_list: list[Any] | None = None
    owners: list[Any] | None = None
    assignment_type: str | None = None
    owner_details: dict[str, Any] | None = None
    created: str | None = None
    timeout: Any | None = None
    timeout_details: Any | None = None
    step_id: int | None = None
    unauthenticated_input: bool | None = None
    agent_id: str | None = None
    is_approval: bool | None = None
    workflow: str | int | None = None  # encrypted run token (list) or numeric run id (retrieve)
    # Present only on the single-item retrieve (``retrieve_wfinput``), not the list:
    input: ManualInputForm | None = None  # the form: {"schema": {title, description, inputVariables}}
    response_mapping: ResponseMapping | None = None  # approval/input options + messages
    custom_fields: dict[str, Any] | None = None  # custom email subject/body/attachment IRIs


class ManualInputResume(ApiResult):
    """The ack from resuming a manual input (``.../wfinput_resume/``).

    Live-verified shape: ``task_id`` (the async resume task) plus the step's
    ``message`` (e.g. ``"Awaiting Playbook resumed successfully."``). Dict-compatible,
    so ``resp["task_id"]`` works alongside ``resp.task_id``.
    """

    task_id: str | None = None
    message: str | None = None


class NotificationPurge(ApiResult):
    """The ack from a system-notification purge (``.../system-notification/purge/``).

    Live-verified shape: ``result`` (human message) and ``status`` (e.g.
    ``"started"`` -- the purge runs asynchronously). Dict-compatible.
    """

    result: str | None = None
    status: str | None = None


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


class ApiKey(BaseRecord):
    """An **API-key binding** record from ``/api/3/api_keys/``.

    This is the *scope* object that binds roles/teams to an API-key user (the
    user record carrying the key material, created via ``/api/auth/users`` —
    :class:`ApiKeyUser`). It is also an **actor**: it's the ``record_type ==
    "ApiKey"`` subtype of the shared ``actors`` table, so a record created via an
    API key expands its ``createUser`` / ``modifyUser`` to this record (``@type ==
    "ApiKey"``, IRI ``/api/3/api_keys/<uuid>`` — live-verified on 8.0.0).
    ``@type`` on the wire is ``ApiKey``; the module slug is ``api_keys``. The
    key value itself is masked on every read here ��� the plaintext lives on the
    API-key user, recoverable only at create time (or via ``show_api_key`` when
    ``retrievable_mode`` was on).
    """

    name: str | None = None
    userId: str | None = None
    roles: list[str] | None = None  # role IRIs (/api/3/roles/<uuid>)
    teams: list[str] | None = None  # team IRIs (/api/3/teams/<uuid>)
    avatar: Any | None = None
    recordTags: list[Any] | None = None
    userType: Any | None = None
    createUser: str | dict[str, Any] | None = None
    createDate: float | None = None
    modifyUser: str | dict[str, Any] | None = None
    modifyDate: float | None = None
    id: int | None = None


#: A FortiSOAR **actor** — any security principal in the shared ``actors`` table.
#: The table is single-table-inheritance keyed on ``record_type``; the three
#: subtypes that surface as expanded relationship targets (``createUser`` /
#: ``modifyUser``) are the human :class:`User` (``@type == "Person"``), the
#: :class:`Appliance` (playbook engine, ``@type == "Appliance"``), and the
#: :class:`ApiKey` actor (``@type == "ApiKey"``, IRI ``/api/3/api_keys/<uuid>`` —
#: what ``createUser`` expands to on a record created via an API key;
#: live-verified on 8.0.0). :meth:`BaseRecord.create_user` / ``modify_user``
#: return this union.
Actor = User | Appliance | ApiKey

# Team.actors references the Actor union, which is defined only here (after the
# subtypes). With deferred annotations pydantic leaves the field unresolved until
# rebuilt, so finalize it now that the union exists.
Team.model_rebuild()


class ApiKeyMaterial(BaseRecord):
    """The nested ``api_key`` block on an :class:`ApiKeyUser`.

    Carries the key value (masked unless read with ``show_api_key`` under
    ``retrievable_mode``) and its validity/status metadata. Modeled as a
    ``BaseRecord`` so ``ak.get("key")`` / ``ak.get("retrievable")`` work — the
    plaintext-recovery helper in :mod:`pyfsr.api.api_keys` relies on that.
    """

    key: str | None = None
    retrievable: bool | None = None
    status: str | None = None  # e.g. "Active"
    valid_until: int | None = None
    time_remaining: int | None = None
    modify_date: int | None = None


class ApiKeyUser(BaseRecord):
    """An **API-key user** from ``/api/auth/users`` (``usersresp[0]``).

    The user record that carries key material (``user_type == 9``), linked to the
    :class:`ApiKey` binding — which is the actual actor-table row (``record_type
    == "ApiKey"``) that shows up on ``createUser``/``modifyUser``. Distinct from a
    People :class:`User`. This ``/api/auth/users`` shape is not a JSON-LD
    ``/api/3`` collection (no ``@id``/``@type`` on the wire), but ``BaseRecord``
    works fine:
    ``id_iri``/``record_type`` stay ``None`` and dict-access (``u["uuid"]``,
    ``u.get("api_key")``) keeps working. The nested ``api_key`` is parsed into
    :class:`ApiKeyMaterial`.
    """

    uuid: str | None = None
    user_type: int | None = None  # 9 = API-key user
    status: int | None = None  # 1 = active
    access_type: str | None = None  # "Concurrent" / "Named"
    loginid: str | None = None
    api_key: ApiKeyMaterial | None = None
    bind_name: str | None = None
    domain: str | None = None
    is_logged_in: bool | None = None
    tenant: Any | None = None


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
    #: Upload timestamp (epoch). The ``/api/3/files`` listing carries this rather
    #: than create/modifyDate (live-verified 8.0.0).
    uploadDate: float | None = None
    thumbnail: Any | None = None  # preview thumbnail ref, null for non-image files
    assignee: str | None = None  # owning user (empty string when unassigned)
    file: Any | None = None
    metadata: Any | None = None
    # create/modifyUser/Date are present on some file responses but absent on the
    # bare /api/3/files listing (which uses uploadDate instead); kept for tolerance.
    createUser: str | dict[str, Any] | None = None
    createDate: float | None = None
    modifyUser: str | dict[str, Any] | None = None
    modifyDate: float | None = None
    id: int | str | None = None


class ExportConnectorRef(ApiResult):
    """One connector entry in an export template's ``options.connectors``.

    Field set captured from a live 7.6.5 export-template ``options.connectors[]``
    entry — every value is a scalar (str/bool/int).
    """

    name: str | None = None
    value: str | None = None
    version: str | None = None
    rpm: bool | None = None
    rpm_name: str | None = None
    rpm_exists: bool | None = Field(default=None, alias="rpmExists")
    exists: bool | None = None
    include: bool | None = None
    include_install: bool | None = Field(default=None, alias="includeInstall")
    install_mode: str | None = None
    installer_path: str | None = None
    configurations: bool | None = None
    config_count: int | None = Field(default=None, alias="configCount")
    configuration_count: int | None = Field(default=None, alias="configurationCount")


class ExportOptions(ApiResult):
    """An export template's selection manifest (``export_template.options``).

    ``connectors`` is modeled (see :class:`ExportConnectorRef`). The manifest's
    other selection lists (``modules``, ``playbooks``, ``roles``, ``views`` …) are
    preserved verbatim in ``extra`` rather than typed, because their element shapes
    have not been captured populated from live wire — they are added here as they
    are observed, never guessed.
    """

    connectors: list[ExportConnectorRef] = []


class Attachment(BaseRecord):
    """An ``/api/3/attachments`` record linking an uploaded :class:`FileRecord`.

    Field set captured from a live 7.6.5 ``/api/3/attachments`` response. ``file``
    is the linked :class:`FileRecord` (the create response expands it; a bare IRI
    string is also accepted). Storage/audit/tenancy keys stay in ``extra``.
    """

    name: str | None = None
    description: str | None = None
    file: FileRecord | str | None = None
    type: str | None = None
    assignee: User | str | None = None
    createUser: str | User | None = None
    createDate: float | None = None
    modifyUser: str | User | None = None
    modifyDate: float | None = None
    id: int | str | None = None

    # FortiSOAR returns ``[]``/``""`` for an unset reference — normalize to None.
    _empty_refs = field_validator("file", "assignee", "createUser", "modifyUser", mode="before")(_empty_to_none)


class ExportTemplate(BaseRecord):
    """An ``/api/3/export_templates`` record — a reusable export selection.

    Field set captured from a live 7.6.5 ``/api/3/export_templates`` response.
    ``options`` is the typed :class:`ExportOptions` selection manifest. Export
    bookkeeping (``metadata``, ``solutionPack``) is preserved in ``extra`` until
    captured populated from live wire.
    """

    name: str | None = None
    options: ExportOptions | None = None
    last_export_date: float | None = Field(default=None, alias="lastExportDate")
    type: str | None = None
    createUser: str | User | None = None
    createDate: float | None = None
    modifyUser: str | User | None = None
    modifyDate: float | None = None
    id: int | str | None = None

    # ``options`` comes back as ``[]`` when empty — normalize to None.
    _empty_options = field_validator("options", mode="before")(_empty_to_none)


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


class RepoConnectorEntry(ContentHubItem):
    """One entry from the public ``connectors.json`` manifest
    (``repo.fortisoar.fortinet.com/connectors/info/connectors.json``).

    Returned by :func:`pyfsr.repo.list_connectors` /
    :func:`pyfsr.repo.search_connectors` — the no-appliance catalog, distinct
    from the on-box :class:`ContentHubConnector` (which needs an appliance).
    The manifest is **latest-version-only** per connector and carries the RPM
    packaging fields the catalog entry doesn't; those are typed here. The
    catalog-shaped fields (``name``/``label``/``version``/``description``/
    ``category``) come from :class:`ContentHubItem`. Dict-compatible.
    """

    path: str | None = None
    rpm_name: str | None = None
    rpm_full_name: str | None = None
    icon: str | None = None

    # The manifest sends ``category`` as a plain string ("Digital assistant")
    # for most entries but as a list (``["Ticket Management"]``) for ~19 of 721
    # — a real shape difference from the on-box catalog (:class:`ContentHubItem`),
    # which always types it as a list. Accept both; normalize via the property.
    category: str | list[Any] | None = None

    @property
    def category_str(self) -> str | None:
        """``category`` flattened to a string (``", "-join`` of a list entry)."""
        c = self.category
        if isinstance(c, list):
            return ", ".join(str(x) for x in c) if c else None
        return c


class WidgetInfo(ApiResult):
    """A widget's published ``info.json`` from Fortinet's public content repo.

    Returned by :func:`pyfsr.repo.widget_info`. Different shape from the
    connector ``info.json`` ��� the widget payload nests human fields under a
    ``metadata`` wrapper (which rides through in ``extra``) and carries a
    ``compatibility`` list instead of ``availableVersions`` (a widget
    ``info.json`` is per-version only; there is no public version-history
    manifest for widgets). Curated fields are typed; the rest stays in
    ``extra``. Dict-compatible.
    """

    name: str | None = None
    title: str | None = None
    subTitle: str | None = None
    version: str | None = None
    description: str | None = None
    compatibility: list[str] | None = None
    publisher: str | None = None
    certified: str | None = None


class SolutionPackInfo(ApiResult):
    """A solution-pack's published ``info.json`` from Fortinet's public content repo.

    Returned by :func:`pyfsr.repo.solution_pack_info`. Carries
    :attr:`availableVersions` (full publish history) plus ``dependencies`` and
    ``fsrMinCompatibility``. Note there is **no public manifest** for solution
    packs and slug resolution is unreliable, so *discovery* (name -> slug)
    still needs :meth:`pyfsr.api.content_hub.ContentHubSearch.search_available_packs`
    on an appliance; this function is the per-version detail lookup once you
    know the slug. Curated fields are typed; the rest (``contents``,
    ``prerequisite``, ``recordTags``, ``featuredTags``, …) stays in ``extra``.
    Dict-compatible.
    """

    name: str | None = None
    label: str | None = None
    version: str | None = None
    description: str | None = None
    availableVersions: list[str] | None = None
    dependencies: list[Any] | None = None
    fsrMinCompatibility: str | None = None
    # Same str-or-list polymorphism as :class:`RepoConnectorEntry.category`.
    category: str | list[Any] | None = None
    publisher: str | None = None
    certified: bool | None = None


class PicklistItem(ApiResult):
    """One item (option) of a picklist, from ``GET /api/3/picklists`` or a create.

    The bulk listing returns every item across every picklist in one page; each
    carries its own ``@id`` (the IRI the API stores on records), its friendly
    ``itemValue``, and the ``listName`` IRI of the picklist it belongs to. Map
    that ``listName`` IRI to a name via ``GET /api/3/picklist_names``. Curated
    fields are typed (``itemValue``/``order_index``/``color``/``icon``); the rest
    of the JSON-LD envelope rides through in ``extra``. Dict-compatible.

    ``order_index`` is the wire ``orderIndex`` (the int sort key). The legacy
    ``ordinal`` attribute is kept as a read alias so existing callers keep working.
    """

    id_iri: str | None = Field(default=None, alias="@id")
    uuid: str | None = None
    itemValue: str | None = None
    # The owning picklist. Usually the listName IRI
    # (``/api/3/picklist_names/<uuid>``); some appliances expand it to a dict.
    # Resolve an IRI to a name via picklist_names. Kept loose so neither shape fails.
    listName: str | dict[str, Any] | None = None
    order_index: int | None = Field(default=None, alias="orderIndex")
    color: str | None = None
    icon: str | None = None

    @property
    def iri(self) -> str | None:
        """The IRI a record stores for this item (``/api/3/picklists/<uuid>``)."""
        if self.id_iri:
            return self.id_iri
        return f"/api/3/picklists/{self.uuid}" if self.uuid else None

    @property
    def list_name_iri(self) -> str | None:
        """The owning picklist's ``listName`` IRI, whether it arrived as a string
        or an expanded ``{@id: ...}`` dict."""
        ln = self.listName
        if isinstance(ln, str):
            return ln
        if isinstance(ln, dict):
            v = ln.get("@id")
            return v if isinstance(v, str) else None
        return None

    @property
    def ordinal(self) -> int | None:
        """Legacy alias for :attr:`order_index` (the wire field is ``orderIndex``)."""
        return self.order_index


class PicklistName(ApiResult):
    """A picklist *list* (the taxonomy an option belongs to), from
    ``GET /api/3/picklist_names`` or a create.

    Each list carries a friendly ``name`` (unique instance-wide — a duplicate POST
    409s with ``UniqueConstraintViolationException``), a ``system`` flag, and its
    ``picklists`` items (embedded only when the request asks for
    ``$relationships=true``; absent/empty otherwise). ``iri`` is the
    ``/api/3/picklist_names/<uuid>`` an option's ``listName`` points back at.
    Dict-compatible; the JSON-LD envelope (``@context``/``@type``/``id``/
    ``importedBy``) rides through in ``extra``.
    """

    id_iri: str | None = Field(default=None, alias="@id")
    uuid: str | None = None
    name: str | None = None
    system: bool | None = None
    # Items embedded under $relationships=true; absent on a bare list (empty []).
    picklists: list[PicklistItem] | None = Field(default=None, alias="picklists")

    @property
    def iri(self) -> str | None:
        """The list's IRI (``/api/3/picklist_names/<uuid>``) — what an option's
        ``listName`` field references."""
        if self.id_iri:
            return self.id_iri
        return f"/api/3/picklist_names/{self.uuid}" if self.uuid else None

    @property
    def items(self) -> list[PicklistItem]:
        """The list's options (embedded under ``$relationships=true``); empty
        when not expanded or the list has none."""
        return self.picklists or []


class SystemViewTemplate(ApiResult):
    """A ``system_view_templates`` row — a module/layout's view configuration.

    From ``GET /api/3/system_view_templates`` (bulk list) or
    ``GET /api/views/1/{name}`` (single named template), used by
    :class:`~pyfsr.api.view_templates.ViewTemplatesAPI`. A "default" is not a
    separate resource — it's this row's ``isDefault`` flag, exactly one of
    which is ``True`` per ``(module, viewOptions)`` pair (verified live, 8.0).

    Template **names are not unique across layouts**: a module ships one
    "Default Layout" row per ``viewOptions`` (``list``/``detail``/``form``),
    so resolving a template by name alone must also scope by ``viewOptions``
    (see ``UserSettingsAPI.resolve_view_template``, which learned this the
    hard way). ``config`` (the layout body — rows/columns/widgets) is typed
    loosely since its shape varies by ``type``; the JSON-LD envelope
    (``@context``/``@type``) rides through ``extra``. Dict-compatible.
    """

    id_iri: str | None = Field(default=None, alias="@id")
    uuid: str | None = None
    name: str | None = None
    module: str | None = None
    #: Layout kind: ``"list"``, ``"detail"``, ``"form"``, or ``"settings"``
    #: (live-verified 8.0).
    viewOptions: str | None = None
    #: Storage type, e.g. ``"rows"`` / ``"form"`` / ``"gridColumns"`` (``None`` for
    #: some rows; live-verified 8.0).
    type: str | None = None
    isDefault: bool | None = None
    #: Platform-shipped flag (vs a user-authored template).
    system: bool | None = None
    #: Whether the template is exposed in the layout picker.
    visible: bool | None = None
    #: The layout body (rows/columns/widgets); shape varies by ``type`` and is a
    #: bare ``[]`` for some modules' empty layouts (live-verified 8.0).
    config: dict[str, Any] | list[Any] | None = None
    #: Record filters scoping this view (the export wizard selects these); usually
    #: ``[]`` for a system default layout.
    filters: list[Any] | None = None

    @property
    def iri(self) -> str | None:
        """The row's IRI (``/api/3/system_view_templates/<uuid>``)."""
        if self.id_iri:
            return self.id_iri
        return f"/api/3/system_view_templates/{self.uuid}" if self.uuid else None


class DailyActionCount(ApiResult):
    """Daily action-count license usage — ``client.system.daily_action_count()``.

    From ``GET /api/wf/workflow/config/?section=license`` (the endpoint the UI's
    ``getDailyActionCount`` calls). Counters are decrypted by the workflow engine.

    ``daily_action_limit`` is the per-day cap enforced by the license (e.g. 10000
    on FortiFlex Starter); ``-1`` means unlimited/unenforced (e.g. an Evaluation
    or edition with no action cap). ``remaining_actions`` counts down as counted
    steps run (Create/Update Record, Connector Action, Set Variable, …; Wait,
    Approval, Loops, and Reference-a-Playbook are not counted). ``reset_time`` is
    the epoch second at which ``remaining_actions`` resets to the limit.
    """

    daily_action_limit: int | None = None
    remaining_actions: int | None = None
    reset_time: int | None = None
    last_update_time: float | None = None

    @property
    def enforced(self) -> bool:
        """True when a positive daily cap is in force (``daily_action_limit > 0``);
        ``-1``/0 mean unlimited or unenforced."""
        return bool(self.daily_action_limit and self.daily_action_limit > 0)

    @property
    def used_today(self) -> int | None:
        """Actions consumed so far today (``daily_action_limit - remaining_actions``),
        or ``None`` when not enforced."""
        if not self.enforced or self.remaining_actions is None:
            return None
        return self.daily_action_limit - self.remaining_actions
