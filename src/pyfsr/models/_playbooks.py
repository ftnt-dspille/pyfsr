"""Typed models for the playbook *run* surface (``client.playbooks``).

Two families live here:

- **Output shapes** (``ApiResult`` subclasses) — the flattened dicts that
  :class:`~pyfsr.api.playbooks.PlaybooksAPI` returns by default. They stay
  dict-compatible (``r["status"]`` / ``r.get(...)`` / ``"pk" in r``) so callers
  written against the old dict API keep working, while new code gets typed
  fields and IDE completion. The full, unshaped run entity is
  :class:`~pyfsr.models.WorkflowRun`; these are the curated *views* of it.

- **Input requests** (``BaseModel`` subclasses) — typed, validated argument
  bundles for the write verbs (trigger / resume / approval / create). Each
  exposes :meth:`to_body` to build the wire payload. ``PlaybooksAPI`` constructs
  these internally from keyword args (mapping pydantic ``ValidationError`` back
  to a friendly :class:`ValueError`), but callers may also build and pass one
  directly.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ._integration import ApiResult

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _alert_iri(ref: str) -> str:
    """Expand a bare alert uuid/ref to a full ``/api/3/alerts/<uuid>`` IRI."""
    if ref.startswith("/api/"):
        return ref
    return f"/api/3/alerts/{ref.rstrip('/').split('/')[-1].split(':')[-1]}"


# ---------------------------------------------------------------- output shapes
class RunSummary(ApiResult):
    """A flattened playbook-run summary (the default :class:`~pyfsr.api.playbooks.PlaybooksAPI` view).

    Produced by ``_shape_run`` for
    :meth:`~pyfsr.api.playbooks.PlaybooksAPI.execution_history`,
    :meth:`~pyfsr.api.playbooks.PlaybooksAPI.last_run`,
    :meth:`~pyfsr.api.playbooks.PlaybooksAPI.wait`, and friends. ``pk`` is the
    trailing segment of the run's ``@id`` (what :meth:`~pyfsr.api.playbooks.PlaybooksAPI.get_execution` takes);
    ``source`` is ``"live"`` or ``"historical"`` (which run table it came from).
    """

    task_id: str | None = None
    name: str | None = None
    status: str | None = None
    error_message: str | None = None
    modified: str | None = None
    uuid: str | None = None
    pk: str | None = None
    source: str | None = None


class RunStep(ApiResult):
    """One step's outcome within a run, as reshaped by :meth:`~pyfsr.api.playbooks.PlaybooksAPI.run_env`."""

    status: str | None = None
    result: Any | None = None


class RunStepSnapshot(ApiResult):
    """A slim per-step outcome snapshot for :meth:`~pyfsr.api.playbooks.PlaybooksAPI.run_tree` (``steps=True``).

    A trimmed preview of a step's result -- enough for an agent to decide whether
    to call :meth:`~pyfsr.api.playbooks.PlaybooksAPI.run_env` for the full detail,
    without the full result bloating the tree. ``result_preview`` is the step's
    ``result`` JSON-encoded and capped to ~500 chars.
    """

    name: str | None = None
    status: str | None = None
    result_preview: str | None = None


class RunEnv(ApiResult):
    """A run's Jinja-context view, from :meth:`~pyfsr.api.playbooks.PlaybooksAPI.run_env`.

    ``env`` is the run's top-level environment (input/request/resources/…);
    ``steps`` is keyed by step display name. In Jinja a step is referenced as
    ``vars.steps.<name with spaces replaced by underscores>``. ``name`` is the
    run's playbook display name (handy for pulling the live playbook back).
    """

    name: str | None = None
    env: dict[str, Any] = Field(default_factory=dict)
    status: str | None = None
    steps: dict[str, RunStep] = Field(default_factory=dict)


class RunNode(ApiResult):
    """One node in a run tree, from :meth:`~pyfsr.api.playbooks.PlaybooksAPI.run_tree`.

    The run plus its referenced-child runs (linked by ``parent_wf``), recursively.
    ``pk`` is the numeric run id; ``children`` are the sub-playbook runs this run
    spawned. Encodes the trigger->run->child linkage so callers don't have to find
    the parent by name in the raw ``/api/wf/api/workflows`` listing.

    ``steps`` carries a slim per-step snapshot (name/status/result_preview) on
    the root node when ``run_tree(steps=True)``; empty otherwise (and always
    empty on child nodes). Call :meth:`~pyfsr.api.playbooks.PlaybooksAPI.run_env`
    for a child's full step detail.
    """

    pk: str | None = None
    name: str | None = None
    status: str | None = None
    task_id: str | None = None
    children: list[RunNode] = Field(default_factory=list)
    steps: list[RunStepSnapshot] = Field(default_factory=list)


class RunFailure(ApiResult):
    """The slim failure projection from :meth:`~pyfsr.api.playbooks.PlaybooksAPI.why_failed`.

    ``failing_step`` is the display name of the first non-success step (``None``
    if the run succeeded); ``error_message`` is the step-level error when present,
    else the run's top-level error.
    """

    status: str | None = None
    failing_step: str | None = None
    error_message: str | None = None
    pk: str | None = None


class TriggerResponse(ApiResult):
    """The response from a trigger verb (``trigger`` / ``trigger_by_name`` / ``trigger_action``).

    Normally ``{"task_id": "<run-uuid>"}``, but a trigger that starts more than
    one run (e.g. an API-endpoint route bound to several playbooks) returns
    ``task_id`` as a **list** of run-uuids — so this accepts either. Extra keys
    (e.g. a deferred 202 envelope) are preserved. Use :attr:`task_ids` for a
    uniform list, or :attr:`task_id` to track the started run with
    :meth:`~pyfsr.api.playbooks.PlaybooksAPI.wait`.

    The routes do not agree on the key. Live-verified on the record-action route
    (``/api/triggers/1/action/<route>``): it answers ``{"task_ids": [...]}`` —
    **plural** — where the manual-execute route (``notrigger``) answers
    ``{"task_id": "..."}``. Because only ``task_id`` was declared, a wire
    ``task_ids`` used to land in ``model_extra`` while the :attr:`task_ids`
    *property* (which normalizes ``task_id``) shadowed it and returned ``[]`` —
    so ``trigger_action`` callers could not reach the run they had just started
    through either accessor. ``_absorb_plural_task_ids`` folds the plural wire
    key into ``task_id`` before validation, making both accessors work for both
    routes and honouring this docstring's "task_id may be a list" contract.
    """

    task_id: str | list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _absorb_plural_task_ids(cls, data: Any) -> Any:
        """Fold the action route's plural ``task_ids`` wire key into ``task_id``."""
        if isinstance(data, dict) and data.get("task_id") is None and data.get("task_ids") is not None:
            data = {**data, "task_id": data["task_ids"]}
        return data

    @property
    def task_ids(self) -> list[str]:
        """``task_id`` normalized to a list (empty when absent)."""
        if self.task_id is None:
            return []
        return list(self.task_id) if isinstance(self.task_id, list) else [self.task_id]


# ------------------------------------------------------------- input requests
class _RequestModel(BaseModel):
    """Base for typed write-request bodies. Forbids unknown keys so a typo'd
    field is caught at construction rather than silently posted."""

    model_config = ConfigDict(extra="forbid")


class TriggerRequest(_RequestModel):
    """Typed body for :meth:`~pyfsr.api.playbooks.PlaybooksAPI.trigger`.

    ``records`` accepts a single ref or a list; bare uuids/refs are expanded to
    ``/api/3/alerts/<uuid>`` IRIs. ``env`` keys are merged into the body verbatim
    for the rare playbook expecting a custom trigger envelope.
    """

    records: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] | None = None
    env: dict[str, Any] = Field(default_factory=dict)

    @field_validator("records", mode="before")
    @classmethod
    def _coerce_records(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return v

    def to_body(self) -> dict[str, Any]:
        """Render the request as the JSON body FortiSOAR's trigger route expects."""
        body: dict[str, Any] = dict(self.env)
        if self.records:
            body["records"] = [_alert_iri(r) for r in self.records]
        if self.inputs is not None:
            body["inputs"] = self.inputs
        return body


class TriggerActionRequest(_RequestModel):
    """Typed body for :meth:`~pyfsr.api.playbooks.PlaybooksAPI.trigger_action`
    (the record-action / ``cybersponse.action`` trigger route)."""

    module: str
    record_uuid: str
    playbook_uuid: str | None = None
    env: dict[str, Any] = Field(default_factory=dict)

    @field_validator("module", "record_uuid")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("must be a non-empty string")
        return v.strip()

    def to_body(self) -> dict[str, Any]:
        """Render the single-record action-trigger JSON body."""
        body: dict[str, Any] = dict(self.env)
        body["singleRecordExecution"] = True
        body["__resource"] = self.module
        body["records"] = [f"/api/3/{self.module}/{self.record_uuid}"]
        if self.playbook_uuid is not None:
            body["__uuid"] = self.playbook_uuid
        return body


class ResumeRequest(_RequestModel):
    """Typed body for :meth:`~pyfsr.api.playbooks.PlaybooksAPI.resume`
    (manual-input / approval resume)."""

    manual_input_id: int
    input: Any = None
    step_iri: str | None = None
    step_id: str | None = None
    approved: bool | None = None

    def to_body(self) -> dict[str, Any]:
        """Render the manual-input/approval resume JSON body."""
        body: dict[str, Any] = {
            "input": self.input,
            "step_iri": self.step_iri,
            "step_id": self.step_id,
            "manual_input_id": int(self.manual_input_id),
        }
        if self.approved is not None:
            body["approved"] = bool(self.approved)
        return body


class ApprovalRequest(_RequestModel):
    """Typed body for :meth:`~pyfsr.api.playbooks.PlaybooksAPI.approval`."""

    decision: str
    comment: str | None = None

    @field_validator("decision")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("approval decision must be a non-empty string")
        return v

    def to_body(self) -> dict[str, Any]:
        """Render the approval-decision JSON body."""
        body: dict[str, Any] = {"decision": self.decision}
        if self.comment is not None:
            body["comment"] = self.comment
        return body


class CreatePlaybookRequest(_RequestModel):
    """Typed body for :meth:`~pyfsr.api.playbooks.PlaybooksAPI.create_playbook`.

    Deliberately **shallow**: it validates the playbook-definition envelope
    (name / collection / flags / picklist IRIs) and passes any other fields
    through verbatim. The deep step/route shape is owned by the ``fsr_playbooks``
    compiler, not this model.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    collection: str
    is_active: bool = True
    remote_executable: bool = False
    priority: str | None = None
    origin: str | None = None

    @field_validator("name")
    @classmethod
    def _name_non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("create_playbook() requires a non-empty name")
        return v.strip()

    @field_validator("collection")
    @classmethod
    def _collection_non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("create_playbook() requires a collection uuid or IRI")
        return v.strip()

    def to_body(self) -> dict[str, Any]:
        """Render the playbook-definition JSON body, expanding the collection to an IRI."""
        coll = self.collection
        coll_iri = coll if coll.startswith("/api/") else f"/api/3/workflow_collections/{coll}"
        body: dict[str, Any] = {
            "name": self.name,
            "collection": coll_iri,
            "isActive": self.is_active,
            "remoteExecutableFlag": self.remote_executable,
        }
        # Pass-through extras (model_config extra="allow").
        body.update(self.__pydantic_extra__ or {})
        if self.priority is not None:
            body["priority"] = self.priority
        if self.origin is not None:
            body["playbookOrigin"] = self.origin
        return body


# --------------------------------------------------------- version-control shapes
class PlaybookVersion(ApiResult):
    """One saved playbook snapshot (the ``workflow_versions`` module).

    FortiSOAR's playbook "version control" is a **snapshot history**, not a
    revision/diff resource: each version is a frozen copy of the playbook
    stored under ``/api/3/workflow_versions`` (capped at 20 per playbook). A
    version is either a manual snapshot (``autosave=False``, a caller-supplied
    ``note``) or an editor auto-save (``autosave=True``).

    ``json`` is the snapshot payload — the full workflow definition
    stringified (``steps`` / ``routes`` / ``groups`` / ``triggerStep`` / …).
    It is populated on ``list_versions`` / ``get_version`` but **not** echoed
    back by ``create_version`` (the server omits the blob on the POST
    response); fetch the version again to read ``json``. ``workflow`` is the
    embedded workflow the snapshot belongs to.
    """

    note: str | None = None
    autosave: bool | None = None
    uuid: str | None = None
    # Stored under ``snapshot_json`` to avoid shadowing pydantic's ``.json()``
    # method; the wire field is ``json`` (alias), and the ``snapshot`` property
    # below exposes it under a name that doesn't clash with BaseModel.
    snapshot_json: str | None = Field(default=None, alias="json")
    workflow: Any | None = None
    create_date: float | None = Field(default=None, alias="createDate")
    modify_date: float | None = Field(default=None, alias="modifyDate")

    @property
    def snapshot(self) -> str | None:
        """The snapshot payload (stringified workflow). Wire field ``json``."""
        return self.snapshot_json

    @property
    def workflow_iri(self) -> str | None:
        """The snapshot's workflow ``@id`` (the playbook it belongs to)."""
        wf = self.workflow
        if isinstance(wf, dict):
            return wf.get("@id")
        return wf if isinstance(wf, str) else None

    def parsed_json(self) -> dict[str, Any]:
        """Decode the snapshot's ``json`` field into the workflow dict.

        Raises ``ValueError`` if ``json`` is absent (e.g. a ``create_version``
        response, which does not echo the blob) — call ``get_version`` first.
        """
        if not self.snapshot_json:
            raise ValueError(
                "version has no json payload (create_version does not echo one); "
                "call get_version() to load the snapshot"
            )
        import json as _json

        return dict(_json.loads(self.snapshot_json))


class VersionStepDelta(ApiResult):
    """One changed step between two playbook versions (:meth:`~pyfsr.api.playbooks.PlaybooksAPI.diff_versions`).

    ``field`` is the top-level step key that differs (``arguments``,
    ``name``, ``stepType``…); ``from`` / ``to`` are the old / new values
    (``Any`` — may be dicts, strings, or ``None``).
    """

    step: str | None = None
    field: str | None = None
    from_value: Any | None = Field(default=None, alias="from")
    to_value: Any | None = Field(default=None, alias="to")


class VersionDiff(ApiResult):
    """A step-graph diff between two playbook snapshots (:meth:`~pyfsr.api.playbooks.PlaybooksAPI.diff_versions`).

    Steps are keyed by ``uuid``. ``added`` / ``removed`` are step uuids present
    in only one side; ``changed`` holds per-step field deltas. ``routes`` /
    ``groups`` are the simpler added/removed-uuid lists for those graphs.
    """

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    changed: list[VersionStepDelta] = Field(default_factory=list)
    routes_added: list[str] = Field(default_factory=list)
    routes_removed: list[str] = Field(default_factory=list)
    groups_added: list[str] = Field(default_factory=list)
    groups_removed: list[str] = Field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        """True when the two snapshots are identical (no added/removed/changed)."""
        return not (
            self.added
            or self.removed
            or self.changed
            or self.routes_added
            or self.routes_removed
            or self.groups_added
            or self.groups_removed
        )


class CreateVersionRequest(_RequestModel):
    """Typed body for :meth:`~pyfsr.api.playbooks.PlaybooksAPI.create_version`.

    Mirrors FortiSOAR's editor ``saveSnapshot`` wire: ``json`` is the prepared
    workflow stringified, ``workflow`` is the workflow IRI, ``modifyDate`` is
    an epoch second timestamp. ``note`` labels the snapshot.
    """

    workflow: str
    # Wire field is ``json``; renamed on the model to avoid shadowing
    # pydantic's ``.json()`` method (see :class:`PlaybookVersion`).
    snapshot_json: str = Field(alias="json")
    note: str = ""
    modify_date: int | None = None

    @field_validator("workflow")
    @classmethod
    def _workflow_non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("create_version() requires a workflow IRI")
        return v

    def to_body(self) -> dict[str, Any]:
        """Render the snapshot-create JSON body (the editor's ``Q()`` shape)."""
        body: dict[str, Any] = {
            "note": self.note,
            "json": self.snapshot_json,
            "workflow": self.workflow,
        }
        if self.modify_date is not None:
            body["modifyDate"] = self.modify_date
        return body
