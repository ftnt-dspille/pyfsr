"""Playbook run history and manual-input resume.

Wraps FortiSOAR's workflow-run surface (``/api/wf/api``). Accessed as
``client.playbooks``.

Run history lives in two tables: ``/workflows/`` holds recent/live runs, but
FortiSOAR purges them to ``/historical-workflows/`` every ~30-60 min (the
historical table also carries richer inline fields). ``execution_history()``
queries both and merges them, deduped by IRI and sorted newest-first.

Example:
    >>> client.playbooks.list(limit=10)                                    # playbook definitions
    >>> client.playbooks.get_definition("<uuid>")                          # one playbook template
    >>> client.playbooks.create_playbooks([payload])                      # re-push definitions
    >>> client.playbooks.execution_history(playbook="Block IP", limit=5)  # one playbook's runs
    >>> client.playbooks.last_run(playbook="Block IP")                    # newest run summary
    >>> client.playbooks.why_failed(playbook="Block IP")                  # newest run's error details
    >>> client.playbooks.get_execution("<run-pk>")                         # one run, full
    >>> client.playbooks.search_executions("High Risk", status="failed")  # filtered search
"""

from __future__ import annotations

import re
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from ..models import (
    CreatePlaybookRequest,
    ManualInputResume,
    ResumeRequest,
    RunEnv,
    RunFailure,
    RunNode,
    RunStep,
    RunSummary,
    TriggerActionRequest,
    TriggerRequest,
    TriggerResponse,
    Workflow,
)
from ..pagination import HydraPage, extract_members
from ..projection import project
from ..query import Query
from .base import BaseAPI


def _pick_approval_option(options: list[dict[str, Any]], decision: str) -> dict[str, Any]:
    """Map an approval ``decision`` to a ``response_mapping`` option dict.

    ``"approve"`` selects the primary option (``primary: true``, else the first);
    ``"reject"`` selects the first non-primary option. Any other string is
    matched against an option's ``option`` label case-insensitively.
    """
    if not options:
        raise LookupError("the pending input exposes no response options")
    d = (decision or "approve").strip().lower()
    for opt in options:
        if (opt.get("option") or "").strip().lower() == d:
            return opt
    if d == "approve":
        for opt in options:
            if opt.get("primary"):
                return opt
        return options[0]
    if d == "reject":
        for opt in options:
            if not opt.get("primary"):
                return opt
        raise LookupError(
            f"no non-primary option to map 'reject' to; options: "
            f"{[(o.get('option'), o.get('primary')) for o in options]}"
        )
    raise LookupError(
        f"no response option matching decision {decision!r}; available: {[o.get('option') for o in options]}"
    )


def _build(model_cls, op: str, **kwargs):
    """Construct a typed request model, re-raising pydantic errors as a friendly
    ``ValueError`` so the SDK keeps its single, predictable exception type."""
    try:
        return model_cls(**kwargs)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ())) or "?"
        raise ValueError(f"{op}(): invalid {loc} — {first.get('msg')}") from e


_TERMINAL_STATUSES = frozenset({"finished", "failed", "error", "cancelled", "aborted"})

# Statuses that mean a step actually FAILED — used by ``why_failed`` to pick the
# real failing step. Deliberately excludes ``incipient``/``pending``/``skipped``/
# ``running``: when a non-last step fails, its downstream steps stay ``incipient``,
# and those must NOT be mistaken for the failure (live-confirmed on run 686500).
_STEP_FAILURE_STATUSES = frozenset({"failure", "failed", "error", "errored", "cancelled", "aborted"})

_RUN_PATHS = ("/api/wf/api/workflows/", "/api/wf/api/historical-workflows/")
# Playbook *definitions* (the templates), distinct from the run-history tables above.
_WORKFLOWS = "/api/3/workflows"
_WORKFLOWS_BULKUPSERT = "/api/3/bulkupsert/workflows"
# A hard delete must also reach already-recycled rows; together they skip the recycle bin.
_HARD_DELETE = {"$hardDelete": "true", "$showDeleted": "true"}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Friendly step-type aliases -> the step-type *name the API filters on*
# (``steps.stepType.name``). These are the names the server actually stores —
# live-verified against /api/3/workflows, so a few differ from the names the
# fsr_playbooks compiler emits (e.g. query name ``ApprovalManualInput`` vs the
# compiler's ``Approval``; the product's ``CyopsUtilites`` is misspelled on the
# wire). An unknown value is passed through verbatim, so the raw API name always
# works too. Cross-ref: fsr_playbooks ``compiler/resolver/_constants.SHORT_TYPE_TO_FSR``.
STEP_TYPE_NAMES: dict[str, str] = {
    "connector": "Connectors",
    "set_variable": "SetVariable",
    "decision": "Decision",
    "find_record": "FindRecords",
    "find_records": "FindRecords",
    "update_record": "UpdateRecord",
    "create_record": "InsertData",
    "insert_record": "InsertData",
    "ingest_bulk_feed": "IngestBulkFeed",
    "delay": "Delay",
    "wait": "Delay",
    "manual_input": "ManualInput",
    "code_snippet": "CodeSnippet",
    "approval": "ApprovalManualInput",
    "workflow_reference": "WorkflowReference",
    "reference": "WorkflowReference",
    "reference_playbook": "WorkflowReference",
    "send_mail": "SendMail",
    "email": "SendMail",
    "utility": "CyopsUtilites",
    "no_op": "CyopsUtilites",
    "set_api_keys": "SetAPIKeys",
}

# Friendly trigger aliases -> the start step's ``triggerStep.stepType.name``.
# The trigger step uses the engine's internal ``cybersponse.*`` names (NOT the
# friendly step names above) — live-verified. Unknown values pass through.
TRIGGER_TYPE_NAMES: dict[str, str] = {
    "manual": "cybersponse.action",  # right-click / Execute menu
    "referenced": "cybersponse.abstract_trigger",  # called by another playbook
    "child": "cybersponse.abstract_trigger",
    "on_create": "cybersponse.post_create",  # record-created trigger
    "on_update": "cybersponse.post_update",  # record-updated trigger
    "api_endpoint": "cybersponse.api_call",  # POST /api/triggers/1/<route>
    "api": "cybersponse.api_call",
}


def _looks_like_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s.strip()))


def _require_uuid(uuid: str, op: str) -> str:
    if not isinstance(uuid, str) or not uuid.strip():
        raise ValueError(f"{op}() requires a non-empty playbook uuid")
    return uuid.strip()


def _pk(pk: str) -> str:
    """Validate + normalize a run / manual-input pk."""
    if not isinstance(pk, str) or not pk.strip():
        raise ValueError("a non-empty pk is required")
    return pk.strip()


# Server-managed fields that must NOT be carried into a clone — the appliance
# assigns them. Left in place they'd either be ignored or cause the POST to look
# like an update of the source row.
_CLONE_STRIP_FIELDS = (
    "@id",
    "id",
    "createDate",
    "modifyDate",
    "lastModifyDate",
    "createUser",
    "modifyUser",
    "deletedAt",
    "versions",
    "owners",
    "importedBy",
    "recordTags",
)


def _collect_uuids(definition: dict[str, Any]) -> set[str]:
    """Every UUID owned by a playbook definition: the workflow + its steps,
    routes, and groups. These are the ids that must be regenerated for a clone."""
    uuids: set[str] = set()
    top = definition.get("uuid")
    if isinstance(top, str) and _looks_like_uuid(top):
        uuids.add(top)
    for key in ("steps", "routes", "groups"):
        for item in definition.get(key) or []:
            u = item.get("uuid") if isinstance(item, dict) else None
            if isinstance(u, str) and _looks_like_uuid(u):
                uuids.add(u)
    return uuids


# Per-entity metadata keys that must be dropped off every nested step/route/group
# of a clone. A nested ``@id`` is the critical one: API-Platform treats an embedded
# child carrying ``@id: /api/3/workflow_steps/<uuid>`` as a *reference to an existing
# entity* and fails the whole POST with ``EntityNotFoundException`` when that uuid
# was regenerated for the clone. The FortiSOAR playbook designer deletes the nested
# ``@id`` on every step/route before saving a copy for exactly this reason (and
# leaves ``stepType`` untouched — it points at a real, shared step-type row).
_CLONE_CHILD_STRIP_FIELDS = ("@id", "@type", "id", "_oldUuid")


def _prepare_clone_body(src: dict[str, Any], *, new_name: str, is_active: bool) -> dict[str, Any]:
    """Build the POST body for a playbook clone from a source definition.

    Regenerates every **owned** UUID (the workflow plus its steps, routes, and
    groups — never the shared ``stepType``) and rewrites all references in one
    pass by substituting old→new UUID strings over the serialized definition.
    Because UUIDs are globally-unique 36-char tokens, a plain string replacement
    safely catches both bare references (route ``sourceStep``/``targetStep``) and
    ones embedded in IRIs (``triggerStep``, step ``group``) without needing to
    know every field that can hold one.

    The nested ``@id``/``@type``/``id`` of each step/route/group is then stripped
    so the appliance creates them fresh rather than treating them as references to
    (now-nonexistent) existing rows — mirroring what the FortiSOAR playbook
    designer does when it duplicates a playbook. Without this the POST fails with
    ``EntityNotFoundException`` for any definition whose steps were inlined
    (e.g. anything fetched with ``$relationships=true``, or imported via YAML).
    """
    import json
    import uuid as _uuid

    remap = {old: str(_uuid.uuid4()) for old in _collect_uuids(src)}
    blob = json.dumps(src)
    for old, new in remap.items():
        blob = blob.replace(old, new)
    body: dict[str, Any] = json.loads(blob)

    for field in _CLONE_STRIP_FIELDS:
        body.pop(field, None)
    body.pop("@type", None)
    body.pop("@context", None)
    for key in ("steps", "routes", "groups"):
        for child in body.get(key) or []:
            if isinstance(child, dict):
                for field in _CLONE_CHILD_STRIP_FIELDS:
                    child.pop(field, None)
    body["name"] = new_name
    body["aliasName"] = None  # the alias (#Name anchor) must be unique; let it re-derive
    body["isActive"] = is_active
    return body


def _shape_run(m: dict[str, Any]) -> RunSummary:
    """Wrap a raw workflow-run record in a typed :class:`RunSummary`.

    The curated fields (``pk``, ``error_message``, ``source``, …) are promoted to
    typed attributes; **every other field of the raw record is preserved** in the
    model's ``extra`` (``RunSummary`` is ``ApiResult`` with ``extra="allow"``), so
    the typed view never loses data — there's no "raw vs typed" trade-off to make.
    """
    res = m.get("result") if isinstance(m.get("result"), dict) else {}
    err = None
    if isinstance(res, dict):
        err = res.get("Error message") or res.get("error") or res.get("message")
    pk_url = m.get("@id") or ""
    pk = pk_url.rstrip("/").rsplit("/", 1)[-1] if pk_url else None
    data = dict(m)
    data.update(error_message=err, pk=pk, source=m.get("_source"))  # "live"/"historical"
    return RunSummary(**data)


class PlaybooksAPI(BaseAPI):
    """Live playbook-run history and resume."""

    def __init__(self, client):
        super().__init__(client)

    # --------------------------------------------------------------- helpers
    def _resolve_uuid(self, playbook: str) -> str | None:
        qs = urllib.parse.urlencode({"name": playbook, "$limit": 5})
        resp = self.client.get(f"{_WORKFLOWS}?{qs}")
        members = (resp or {}).get("hydra:member") or []
        return members[0].get("uuid") if members else None

    def resolve_iri(self, playbook: str) -> str | None:
        """Resolve a playbook name to its workflow IRI (``/api/3/workflows/<uuid>``).

        Returns ``None`` if no playbook with that name exists. Use when an
        operation needs the IRI rather than the uuid -- e.g. scheduling a
        periodic task whose ``kwargs.wf_iri`` points at the workflow.
        """
        uuid = self._resolve_uuid(playbook)
        return f"{_WORKFLOWS}/{uuid}" if uuid else None

    # ------------------------------------------------------ definition CRUD
    def list(
        self,
        *,
        name: str | None = None,
        collection: str | None = None,
        limit: int = 50,
        relationships: bool = False,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """List playbook **definitions** (``GET /api/3/workflows``), newest table order.

        These are the playbook templates, not run history (see :meth:`runs`). Filter by
        ``name`` (exact) or ``collection`` (a collection uuid; the bare uuid or a full
        ``/api/3/workflow_collections/<uuid>`` IRI both work). ``relationships=True`` adds
        ``$relationships=true`` so each workflow's ``steps``/``routes`` come back inline
        (heavier). Pass ``params=`` to forward additional API-Platform filters such as
        ``triggerStep.stepType.name=...`` or ``$fields=...``. Returns the ``hydra:member``
        array.
        """
        query: dict[str, Any] = dict(params or {})
        query["$limit"] = limit
        if name is not None:
            query["name"] = name
        if collection is not None:
            query["collection"] = collection.rstrip("/").rsplit("/", 1)[-1]
        if relationships:
            query["$relationships"] = "true"
        return extract_members(self.client.get(_WORKFLOWS, params=query))

    def find(
        self,
        *,
        name: str | None = None,
        name_contains: str | None = None,
        collection: str | None = None,
        tag: str | None = None,
        active: bool | None = None,
        private: bool | None = None,
        trigger_type: str | None = None,
        step_type: str | None = None,
        uses_connector: str | None = None,
        uses_operation: str | None = None,
        route: str | None = None,
        references: str | None = None,
        remote_executable: bool | None = None,
        single_record: bool | None = None,
        limit: int = 50,
        relationships: bool = False,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search playbook definitions across the most useful dimensions at once.

        A thin, ergonomic layer over the FortiSOAR API-Platform deep-relationship
        filter language (all filters are ANDed; each maps to a ``/api/3/workflows``
        query param). Every argument is optional; pass only what you want to
        constrain. All values are **live-verified** against the query API.

        Args:
            name: exact playbook name.
            name_contains: case-insensitive substring of the name (``name$like``).
            collection: collection uuid or ``/api/3/workflow_collections/<uuid>`` IRI.
            tag: a tag substring (``tag$like``).
            active: ``True`` for active playbooks only, ``False`` for disabled.
            private: ``True`` for private (owner-scoped) playbooks, ``False`` for public.
            trigger_type: filter on the **start step** — a friendly alias from
                ``TRIGGER_TYPE_NAMES`` (``manual``, ``on_create``, ``on_update``,
                ``referenced``, ``api_endpoint``) or a raw ``cybersponse.*`` name.
            step_type: playbooks **containing** a step of this type — a friendly
                alias from ``STEP_TYPE_NAMES`` (``connector``, ``decision``,
                ``manual_input``, ``approval``, ``reference``, ``code_snippet``,
                …) or a raw API step-type name.
            uses_connector: playbooks with a step invoking this connector (matched
                as a substring of step ``arguments``, e.g. ``"fortigate"``).
            uses_operation: playbooks with a step calling this connector operation
                (substring of ``arguments``, e.g. ``"block_ip"``).
            route: playbooks exposing this API-endpoint route (implies an
                ``api_endpoint`` trigger; substring of ``arguments``).
            references: playbooks that reference another playbook by name (implies
                a ``reference`` step; substring of ``arguments``).
            remote_executable: agent/remote-executable playbooks only.
            single_record: single-record-execution playbooks only.
            limit: max results (default 50).
            relationships: inline each playbook's ``steps``/``routes`` (heavier).
            params: extra raw query params, merged last (escape hatch).

        Returns:
            The matching playbook-definition records (``hydra:member``).

        Note:
            ``arguments``-substring filters (``uses_connector``, ``uses_operation``,
            ``route``, ``references``) all target the same JSON column, so at most
            **one** may be combined with the others per call — passing two raises
            ``ValueError``. For richer boolean logic use :meth:`query` with a
            :class:`~pyfsr.query.Query`.
        """
        q: dict[str, Any] = {}
        if name_contains is not None:
            q["name$like"] = f"%{name_contains}%"
        if tag is not None:
            q["tag$like"] = f"%{tag}%"
        if active is not None:
            q["isActive"] = "true" if active else "false"
        if private is not None:
            q["isPrivate"] = "true" if private else "false"
        if remote_executable is not None:
            q["remoteExecutableFlag"] = "true" if remote_executable else "false"
        if single_record is not None:
            q["singleRecordExecution"] = "true" if single_record else "false"
        if trigger_type is not None:
            q["triggerStep.stepType.name"] = TRIGGER_TYPE_NAMES.get(trigger_type.lower(), trigger_type)
        if step_type is not None:
            q["steps.stepType.name"] = STEP_TYPE_NAMES.get(step_type.lower(), step_type)

        # All of these match a substring of the shared JSON `arguments` column,
        # so only one $like on `steps.arguments` can live in the param dict.
        arg_likes = {
            "uses_connector": uses_connector,
            "uses_operation": uses_operation,
            "route": route,
            "references": references,
        }
        supplied = {k: v for k, v in arg_likes.items() if v is not None}
        if len(supplied) > 1:
            raise ValueError(
                "find(): only one of uses_connector/uses_operation/route/references "
                f"can be combined per call (got {sorted(supplied)}); use query() for "
                "multiple step-argument conditions"
            )
        if supplied:
            ((_, val),) = supplied.items()
            q["steps.arguments$like"] = f"%{val}%"
        # `route`/`references` further imply a specific step/trigger; only set the
        # implied type when the caller didn't already constrain it themselves.
        if route is not None and trigger_type is None:
            q["triggerStep.stepType.name"] = TRIGGER_TYPE_NAMES["api_endpoint"]
        if references is not None and step_type is None:
            q["steps.stepType.name"] = STEP_TYPE_NAMES["reference"]

        if params:
            q.update(params)
        return self.list(name=name, collection=collection, limit=limit, relationships=relationships, params=q)

    def find_with_step_type(self, step_type: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Playbooks containing at least one step of ``step_type`` (see :meth:`find`)."""
        return self.find(step_type=step_type, **kwargs)

    def find_by_trigger_type(self, trigger_type: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Playbooks whose start step is ``trigger_type`` (see :meth:`find`)."""
        return self.find(trigger_type=trigger_type, **kwargs)

    def find_using_connector(
        self, connector: str, *, operation: str | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Playbooks with a step that invokes ``connector`` (optionally a specific
        ``operation``). Matched as a substring of the step ``arguments`` — pass the
        connector slug as it appears on the wire (e.g. ``"fortigate"``,
        ``"fortinet-fortimanager"``). ``operation`` and ``connector`` can't both be
        used here (same JSON column); pass whichever is more selective."""
        if operation is not None:
            return self.find(uses_operation=operation, **kwargs)
        return self.find(uses_connector=connector, **kwargs)

    def find_referencing(self, playbook: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Playbooks that reference ``playbook`` (by name) via a reference step."""
        return self.find(references=playbook, **kwargs)

    def find_by_route(self, route: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Playbooks exposing the API-endpoint ``route`` (``POST /api/triggers/1/<route>``)."""
        return self.find(route=route, **kwargs)

    def match(
        self,
        predicate: Callable[[Any], bool],
        *,
        prefilter: dict[str, Any] | None = None,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Structural search the server filter language can't express.

        Fetches playbook definitions **with steps inlined**, parses each into a
        :class:`~pyfsr.playbook_match.ParsedPlaybook`, and returns the raw
        definitions whose parse satisfies ``predicate`` (built from
        :mod:`pyfsr.playbook_match` helpers — ``step``/``count``/``has``/
        ``trigger``/``all_of``/``any_of``/``none_of``).

        Use this for same-step precision ("fortigate AND block_ip on one step"),
        quantities ("exactly 2 set-variable steps"), or any boolean mix. Pass
        ``prefilter`` (a :meth:`find` kwargs dict) to narrow server-side first and
        avoid pulling every playbook — e.g. ``prefilter={"trigger_type": "manual"}``.

        Example:
            >>> from pyfsr.playbook_match import step, count, all_of
            >>> pred = all_of(count(step(step_type="set_variable"), n=2),
            ...               count(step(step_type="code_snippet"), n=1))
            >>> client.playbooks.match(pred)  # doctest: +SKIP
        """
        from ..playbook_match import parse_playbook

        kwargs = dict(prefilter or {})
        kwargs.pop("relationships", None)
        defs = self.find(limit=limit, relationships=True, **kwargs)
        return [d for d in defs if predicate(parse_playbook(d))]

    def match_across(
        self,
        parent_predicate: Callable[[Any], bool],
        child_predicate: Callable[[Any], bool],
        *,
        limit: int = 2000,
    ) -> list[dict[str, Any]]:
        """Parent/child join: parents matching ``parent_predicate`` that reference a
        child matching ``child_predicate``.

        Pulls the playbook corpus once (with steps), resolves each parent's
        reference steps to children by name, and returns the raw parent
        definitions. Answers questions like "a manual playbook whose referenced
        child blocks an IP"::

            from pyfsr.playbook_match import trigger, has, step
            client.playbooks.match_across(
                trigger("manual"), has(step(operation="block_ip")))
        """
        from ..playbook_match import join_parent_child, parse_playbook

        corpus = [parse_playbook(d) for d in self.find(limit=limit, relationships=True)]
        matched = join_parent_child(corpus, parent_predicate, child_predicate)
        return [pb.raw for pb in matched]

    def get_definition(
        self,
        uuid: str,
        *,
        relationships: bool = True,
    ) -> Workflow:
        """Fetch one playbook definition by uuid (``GET /api/3/workflows/{uuid}``).

        Returns a typed :class:`~pyfsr.models.Workflow`. It stays dict-compatible
        (``wf["name"]`` / ``wf.get(...)``) and round-trips to a plain dict via
        ``wf.to_dict()`` when a JSON-serializable copy is needed (e.g. by
        :meth:`clone`). ``relationships=True`` (default) inlines the workflow's
        steps/routes/groups, which is the usual shape callers want when inspecting
        or cloning a playbook.
        """
        uuid = _require_uuid(uuid, "get_definition")
        params = {"$relationships": "true"} if relationships else None
        resp = self.client.get(f"{_WORKFLOWS}/{uuid}", params=params)
        return Workflow(**resp)

    def create_playbook(
        self,
        name: str,
        collection: str,
        *,
        is_active: bool = True,
        remote_executable: bool = False,
        priority: str | None = None,
        origin: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """Create a single playbook definition (``POST /api/3/workflows``).

        Args:
            name: display name of the playbook.
            collection: the collection to place it in — a uuid or full
                ``/api/3/workflow_collections/<uuid>`` IRI.
            is_active: whether the playbook is active (default ``True``).
            remote_executable: allow remote agent execution (default ``False``).
            priority: priority picklist IRI (e.g.
                ``/api/3/picklists/<uuid>``). Omit to let the appliance use
                its default.
            origin: playbookOrigin picklist IRI. Omit for the appliance default.
            **fields: any additional fields to merge into the POST body verbatim.

        Returns:
            The created playbook definition record.
        """
        req = _build(
            CreatePlaybookRequest,
            "create_playbook",
            name=name,
            collection=collection,
            is_active=is_active,
            remote_executable=remote_executable,
            priority=priority,
            origin=origin,
            **fields,
        )
        return self.client.post(_WORKFLOWS, data=req.to_body())

    def clone(
        self,
        uuid: str,
        new_name: str,
        *,
        collection: str | None = None,
        is_active: bool = False,
        transform: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        """Clone an existing playbook definition under a new name.

        Fetches the source playbook with its steps/routes/groups inlined, then
        **remaps every owned UUID** (the workflow itself plus each step, route,
        and group — never the shared ``stepType``) to a fresh one, rewiring all
        the internal references — route ``sourceStep``/``targetStep``, the
        workflow's ``triggerStep``, and each step's ``group`` — so the copy is
        fully self-contained and never collides with the original. Server-managed
        fields (``@id``/``id``/create+modify stamps, ``deletedAt``, ``versions``)
        are dropped at the top level, and the nested ``@id``/``@type``/``id`` of
        every step/route/group is stripped so the appliance creates them fresh
        (otherwise an inlined child carrying an ``@id`` is read as a reference to
        a now-nonexistent row and the POST fails with ``EntityNotFoundException``).
        POSTs the result to ``/api/3/workflows``.

        Args:
            uuid: source playbook uuid.
            new_name: display name for the clone (required — a clone must be
                distinguishable from its source).
            collection: optionally re-home the clone into a different collection
                (uuid or IRI). Defaults to the source's collection.
            is_active: whether the clone is active. Defaults to ``False`` so a
                copy never starts firing on triggers before it's been reviewed.
            transform: optional callback to mutate the prepared POST body
                **before** it is sent — receives the body dict (already remapped,
                stripped, and renamed) and may edit it in place or return a
                replacement. Use it to tweak a cloned definition at create time,
                e.g. rename a Set-Variable arg, so the change is part of the same
                save the appliance validates.

        Returns:
            The created clone's playbook definition record.
        """
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("clone() requires a non-empty new_name")
        uuid = _require_uuid(uuid, "clone")
        src = self.get_definition(uuid, relationships=True).to_dict(by_alias=True)

        body = _prepare_clone_body(src, new_name=new_name.strip(), is_active=is_active)
        if collection is not None:
            if not isinstance(collection, str) or not collection.strip():
                raise ValueError("clone() collection must be a uuid or IRI")
            body["collection"] = (
                collection if collection.startswith("/api/") else f"/api/3/workflow_collections/{collection}"
            )
        if transform is not None:
            body = transform(body) or body
        return self.client.post(_WORKFLOWS, data=body)

    def update(self, uuid: str, **fields: Any) -> dict[str, Any]:
        """Partially update a playbook definition (``PUT /api/3/workflows/{uuid}``).

        Pass only the keys to change, e.g. ``debug=True``, ``isActive=False``,
        ``name=...``.
        """
        uuid = _require_uuid(uuid, "update")
        if not fields:
            raise ValueError("update() requires at least one field to change")
        return self.client.put(f"{_WORKFLOWS}/{uuid}", data=fields)

    def delete(self, uuid: str, *, hard: bool = True) -> None:
        """Delete a playbook definition. ``hard=True`` (default) bypasses the recycle bin.

        Sends **no request body** — the appliance silently no-ops a delete carrying a ``{}``
        body and leaks the row, so this never passes one. ``hard=False`` does a soft
        (recycle-bin) delete.
        """
        uuid = _require_uuid(uuid, "delete")
        params = dict(_HARD_DELETE) if hard else None
        self.client.delete(f"{_WORKFLOWS}/{uuid}", params=params)

    def create_playbooks(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Create or re-push many playbook definitions (``POST /api/3/bulkupsert/workflows``).

        Pass the workflow rows exactly as they would appear in a collection payload.
        """
        return self.client.post(_WORKFLOWS_BULKUPSERT, data=rows)

    def query(
        self,
        query: Query | dict[str, Any],
        *,
        page: int = 1,
        raw: bool = False,
        fields: list[str] | tuple[str, ...] | None = None,
        summary: bool = False,
        show_deleted: bool = False,
    ) -> Any:
        """Run a structured query against ``/api/query/workflows``.

        Mirrors :meth:`pyfsr.records.RecordSet.query` for the workflow-definition
        surface. Members come back as typed :class:`~pyfsr.models.Workflow` objects;
        ``raw=True`` returns the whole :class:`~pyfsr.pagination.HydraPage` instead of
        just its members, and ``fields``/``summary`` apply a token-efficient
        projection (returning trimmed dicts) for agent reads.
        """
        body = query.to_body() if isinstance(query, Query) else dict(query)
        params: dict[str, Any] = {"$page": page}
        limit = body.pop("limit", None)
        if limit is not None:
            params["$limit"] = limit
        search = body.pop("search", None)
        if search is not None:
            params["$search"] = search
        if show_deleted:
            params["$showDeleted"] = "true"
            body["showDeleted"] = True
        resp = self.client.post("/api/query/workflows", data=body, params=params)
        page_obj = HydraPage.from_response(resp, page=page, limit=params.get("$limit"))
        if fields or summary:
            return project(page_obj, fields=fields, summary=summary)
        page_obj.members = [Workflow(**m) if isinstance(m, dict) else m for m in page_obj.members]
        return page_obj if raw else page_obj.members

    def _fetch_runs_both(
        self, *, limit: int, extra_qs: str = "", parent_filter: str = "parent_wf__isnull=True"
    ) -> list[dict[str, Any]]:
        """Fetch + merge ``/workflows/`` and ``/historical-workflows/``.

        ``parent_filter`` is the run-tree scope clause appended to the query.
        The default (``parent_wf__isnull=True``) returns only top-level runs —
        async sub-playbook children are excluded. Pass ``parent_wf=<pk>`` to
        fetch the children of one run, or ``""`` for an unscoped list.
        """
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in _RUN_PATHS:
            scope = f"&{parent_filter}" if parent_filter else ""
            qs = f"?format=json&limit={limit}&ordering=-modified{scope}{extra_qs}"
            try:
                resp = self.client.get(path + qs)
            except Exception:  # noqa: BLE001 - one table being down shouldn't blank the other
                continue
            for m in (resp or {}).get("hydra:member") or []:
                iri = m.get("@id") or ""
                if iri and iri in seen:
                    continue
                seen.add(iri)
                m["_source"] = "historical" if "historical" in path else "live"
                out.append(m)
        out.sort(key=lambda m: m.get("modified") or "", reverse=True)
        return out

    # ---------------------------------------------------------------- reads
    def execution_history(
        self,
        *,
        playbook: str | None = None,
        playbook_uuid: str | None = None,
        limit: int = 20,
    ) -> list[RunSummary]:
        """List recent playbook executions, newest first (live + historical merged).

        Scope to one playbook by ``playbook`` (name, resolved to uuid) or
        ``playbook_uuid``. Returns typed :class:`~pyfsr.models.RunSummary` objects
        — the curated fields (``task_id``/``status``/``error_message``/``pk``/…) as
        typed attributes, with the full raw run record preserved in ``extra`` and
        reachable by item access (``run["created"]``).
        """
        extra = ""
        if playbook_uuid or playbook:
            if not playbook_uuid:
                playbook_uuid = self._resolve_uuid(playbook)
                if not playbook_uuid:
                    return []
            extra = f"&template_iri=/api/3/workflows/{playbook_uuid}"
        members = self._fetch_runs_both(limit=limit, extra_qs=extra)[:limit]
        return [_shape_run(m) for m in members]

    def child_runs(
        self,
        parent: str | int,
        *,
        limit: int = 100,
    ) -> list[RunSummary]:
        """List the child executions spawned by one parent run, newest first.

        A loop step with ``parallel`` + ``apply_async`` (or any ``apply_async``
        workflow_reference) records each sub-playbook invocation as its OWN
        execution, linked to the parent by ``parent_wf``. :meth:`execution_history`
        filters these out (it scopes to ``parent_wf__isnull=True``), so this is the
        method to retrieve them — e.g. to measure loop max-parallel concurrency by
        feeding the returned runs (``created``/``modified`` timestamps) to
        :func:`pyfsr.concurrency.compute_overlap`.

        The parent run is tagged ``#has_async_childwf_cyops`` when it has async
        children; see :meth:`has_async_children`.

        Args:
            parent: the parent run — a numeric run pk (``210`` / ``"210"``), a run
                ``@id``/path (``"/wf/api/workflows/210/"``), or a ``task_id`` uuid
                (resolved to its pk via the live log).
            limit: maximum number of children to return (default 100).

        Returns:
            A list of typed :class:`~pyfsr.models.RunSummary` children, newest
            first. Empty if the parent has no async children (or isn't found).
        """
        pk = self._resolve_run_pk(parent)
        if pk is None:
            return []
        members = self._fetch_runs_both(limit=limit, parent_filter=f"parent_wf={pk}")[:limit]
        return [_shape_run(m) for m in members]

    def has_async_children(self, parent: str | int) -> bool:
        """Whether a run dispatched async sub-playbook children.

        Checks for the ``#has_async_childwf_cyops`` tag FortiSOAR stamps on a run
        once it launches an ``apply_async`` child — the same signal the UI uses to
        decide whether to offer a child-run drill-down. ``parent`` accepts the same
        forms as :meth:`child_runs`.
        """
        pk = self._resolve_run_pk(parent)
        if pk is None:
            return False
        try:
            run = self.get_execution(str(pk))
        except Exception:  # noqa: BLE001 - absence is a clean "no"
            return False
        return "has_async_childwf_cyops" in str(run.get("tags") or "")

    def _resolve_run_pk(self, parent: str | int) -> str | None:
        """Coerce a pk / @id-path / task_id into a numeric run pk string."""
        if isinstance(parent, int):
            return str(parent)
        s = str(parent).strip()
        if not s:
            return None
        if s.isdigit():
            return s
        if "/" in s:  # an @id or path like /wf/api/workflows/210/
            tail = s.rstrip("/").rsplit("/", 1)[-1]
            return tail if tail.isdigit() else None
        if _looks_like_uuid(s):  # a task_id — map to its pk via the live log
            resp = self.log_list(task_id=s, limit=1)
            members = (resp or {}).get("hydra:member") or []
            if members:
                iri = members[0].get("@id") or ""
                tail = iri.rstrip("/").rsplit("/", 1)[-1]
                return tail if tail.isdigit() else None
        return None

    def get_execution(
        self,
        run_pk: str,
        *,
        step_detail: bool = False,
    ) -> RunSummary:
        """Fetch one playbook execution by its pk.

        The pk is the trailing segment of a run's ``@id`` URL. Tries the live
        table first, then historical. Returns a typed
        :class:`~pyfsr.models.RunSummary` — curated fields as attributes, the full
        raw record preserved in ``extra`` (so ``run["result"]`` etc. still work).

        Pass ``step_detail=True`` to include the per-step execution trace; the
        per-step results then ride in the model's ``extra`` under ``steps``/``env``
        (see :meth:`run_env` for a reshaped view of them).
        """
        if not isinstance(run_pk, str) or not run_pk.strip():
            raise ValueError("get_execution() requires a non-empty run pk")
        run_pk = run_pk.strip()
        suffix = "&step_detail=true" if step_detail else ""
        last_err: Exception | None = None
        for path in _RUN_PATHS:
            try:
                resp = self.client.get(f"{path}{run_pk}/?format=json{suffix}")
            except Exception as e:  # noqa: BLE001 - fall through to historical
                last_err = e
                continue
            if isinstance(resp, dict) and resp.get("@id"):
                resp["_source"] = "historical" if "historical" in path else "live"
                return _shape_run(resp)
        if last_err is not None:
            raise last_err
        raise ValueError(f"execution {run_pk!r} not found")

    def search_executions(
        self,
        query: str | None = None,
        *,
        tags_include: str | list[str] | None = None,
        tags_exclude: str | list[str] | None = None,
        status: str | None = None,
        playbook: str | None = None,
        playbook_uuid: str | None = None,
        limit: int = 20,
        offset: int = 0,
        ordering: str = "-modified",
    ) -> list[RunSummary]:
        """Search playbook execution history with human-friendly filters.

        Queries ``POST /api/wf/api/workflows/log_list/`` and returns shaped run
        dicts. All filters are optional and combinable.

        Args:
            query: free-text search across playbook name / run metadata
                (forwarded as ``search=``).
            tags_include: tag name(s) a run must have (comma-joined string or
                list of strings).
            tags_exclude: tag name(s) to exclude (same format).
            status: execution status filter, e.g. ``"finished"``, ``"failed"``,
                ``"Running"``.
            playbook: playbook name — resolved to a uuid and forwarded as
                ``template_iri``.
            playbook_uuid: playbook uuid (use instead of ``playbook`` when you
                already have it).
            limit: max results (default 20).
            offset: page offset.
            ordering: sort field (default ``"-modified"`` = newest first).

        Returns:
            List of shaped run dicts
            (``{task_id, name, status, error_message, modified, uuid, pk, source}``).
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "ordering": ordering,
            "format": "json",
        }
        if query is not None:
            params["search"] = query
        if status is not None:
            params["status"] = status
        if tags_include is not None:
            params["tags_include"] = ",".join(tags_include) if isinstance(tags_include, list) else tags_include
        if tags_exclude is not None:
            params["tags_exclude"] = ",".join(tags_exclude) if isinstance(tags_exclude, list) else tags_exclude
        if playbook_uuid or playbook:
            if not playbook_uuid:
                playbook_uuid = self._resolve_uuid(playbook)
            if playbook_uuid:
                params["template_iri"] = f"/api/3/workflows/{playbook_uuid}"
        resp = self.client.post("/api/wf/api/workflows/log_list/", data={}, params=params)
        members = extract_members(resp)
        return [_shape_run(m) for m in members]

    def last_run(
        self,
        playbook: str | None = None,
        *,
        playbook_uuid: str | None = None,
    ) -> RunSummary | None:
        """Return the most recent run for a playbook as a typed :class:`~pyfsr.models.RunSummary`.

        Fetches the most recent execution (merged from live + historical tables,
        newest first) for the specified playbook. The playbook may be identified
        by ``playbook`` (name, resolved to uuid) or ``playbook_uuid``. The full raw
        run record is preserved in the model's ``extra`` (e.g. ``run["@id"]``).

        Args:
            playbook: the playbook name — resolved to uuid internally.
            playbook_uuid: the playbook uuid (use instead of ``playbook`` when
                you already have it).

        Returns:
            The :class:`~pyfsr.models.RunSummary` for the most recent execution,
            or ``None`` if the playbook has no runs or does not exist.

        Example:
            >>> run = client.playbooks.last_run(playbook="Block IP")
            >>> if run:
            ...     print(f"{run.name}: {run.status} ({run.pk})")
        """
        runs = self.execution_history(
            playbook=playbook,
            playbook_uuid=playbook_uuid,
            limit=1,
        )
        return runs[0] if runs else None

    def why_failed(
        self,
        playbook: str | None = None,
        *,
        playbook_uuid: str | None = None,
    ) -> RunFailure | None:
        """Find the most recent run and return its error details as a typed :class:`~pyfsr.models.RunFailure`.

        Locates the most recent execution for the specified playbook, then
        calls :meth:`get_execution` with ``step_detail=True`` to retrieve the
        full record (error_message and step results are only fully populated
        there). The playbook may be identified by ``playbook`` (name, resolved
        to uuid) or ``playbook_uuid``. For the full run record (all steps/env),
        call :meth:`get_execution` with the returned ``pk``.

        Args:
            playbook: the playbook name — resolved to uuid internally.
            playbook_uuid: the playbook uuid (use instead of ``playbook`` when
                you already have it).

        Returns:
            A :class:`~pyfsr.models.RunFailure` with ``status`` (the run's terminal
            status), ``error_message`` (from the run result or first failing step;
            ``None`` if it succeeded), ``failing_step`` (name of the first non-success
            step, ``None`` if it succeeded), and ``pk``. Returns ``None`` if the
            playbook has no runs or does not exist.

        Example:
            >>> failure = client.playbooks.why_failed(playbook="Block IP")
            >>> if failure and failure.failing_step:
            ...     print(f"Run {failure.pk} failed at {failure.failing_step}: {failure.error_message}")
        """
        run = self.last_run(playbook=playbook, playbook_uuid=playbook_uuid)
        if not run:
            return None

        pk = run.get("pk") or (run.get("@id") or "").rstrip("/").rsplit("/", 1)[-1]
        if not pk:
            return None

        full = self.get_execution(pk, step_detail=True)

        # Extract the first failing step, if any.
        failing_step = None
        failing_step_msg = None
        for step in full.get("steps") or []:
            if not isinstance(step, dict):
                continue
            status = (step.get("status") or "").lower()
            # Match only ACTUAL failure statuses. A non-last step failing leaves its
            # downstream steps ``incipient``/``pending``/``skipped`` — those are not
            # failures and must be skipped, else the wrong step is reported.
            if status in _STEP_FAILURE_STATUSES:
                failing_step = step.get("name")
                result = step.get("result") or {}
                if isinstance(result, dict):
                    failing_step_msg = result.get("Error message") or result.get("error") or result.get("message")
                break

        # Extract error message from the run's top-level result.
        res = full.get("result") if isinstance(full.get("result"), dict) else {}
        top_error = None
        if isinstance(res, dict):
            top_error = res.get("Error message") or res.get("error") or res.get("message")

        # Use the step-level message if available, else fall back to top-level.
        error_message = failing_step_msg or top_error

        return RunFailure(
            status=full.get("status"),
            failing_step=failing_step,
            error_message=error_message,
            pk=pk,
        )

    def run_env(self, run: str | int) -> RunEnv:
        """Return a run's execution environment + per-step results.

        ``run`` may be a run pk, an ``@id`` path, or a ``task_id`` (what
        :meth:`trigger`/:meth:`trigger_by_name` return) — it is resolved the
        same way as :meth:`step_status`/:meth:`child_runs`, so you can pass a
        ``task_id`` straight through without resolving the pk yourself.

        Fetches the run with ``step_detail=true`` and reshapes it into the
        Jinja-context view used when authoring/debugging a playbook::

            {
              "env":   {...},                      # the run's top-level env dict
                                                   # (input, request, resources, …)
              "status": "finished",
              "steps": {                           # keyed by step display name
                "Step Name": {"status": ..., "result": {...}},
                ...
              },
            }

        Step names are returned verbatim; in Jinja they are referenced as
        ``vars.steps.<name with spaces replaced by underscores>``.

        .. note::
            Runtime ``set_variable`` / jinja values are only captured in the
            retrievable run record when the appliance has **global workflow debug
            logging enabled**; with it off (the default), ``env`` and a step's
            ``result`` come back empty for them. So unless you've turned debug
            logging on, assert on the *verifiable* signal -- the step's
            ``status`` -- via :meth:`step_status` rather than a value that may not
            be recorded.
        """
        pk = self._resolve_run_pk(run)
        if pk is None:
            raise ValueError(f"could not resolve a run pk from {run!r}")
        full = self.get_execution(str(pk), step_detail=True)
        steps: dict[str, RunStep] = {}
        for s in full.get("steps") or []:
            if not isinstance(s, dict):
                continue
            name = s.get("name")
            if not name:
                md = s.get("metadata") or {}
                name = (md.get("metadata") or {}).get("name") or md.get("name")
            if not name:
                continue
            steps[name] = RunStep(status=s.get("status"), result=s.get("result"))
        return RunEnv(
            env=full.get("env") or {},
            status=full.get("status"),
            steps=steps,
        )

    def step_status(self, run: str | int, step_name: str) -> str | None:
        """The status of one step in a run (or ``None`` if the step isn't found).

        Use this to assert on a step's *verifiable* outcome rather than chasing a
        runtime value: FortiSOAR only records ``set_variable`` / jinja values in
        the retrievable run record when **global workflow debug logging is
        enabled**, so ``run_env(...).env`` is empty for them with debug logging
        off (the default). A step's ``status`` (``"finished"``, ``"failed"``, …)
        always survives — assert on that instead.

        Args:
            run: a run pk / ``@id`` path / ``task_id`` (resolved like
                :meth:`child_runs`).
            step_name: the step's display ``name``.
        """
        pk = self._resolve_run_pk(run)
        if pk is None:
            return None
        env = self.run_env(str(pk))
        step = env.steps.get(step_name)
        return step.status if step else None

    def run_tree(
        self,
        run: str | int,
        *,
        depth: int = 3,
        limit: int = 100,
    ) -> RunNode:
        """Resolve a run to its execution tree: the run plus its child runs.

        Returns a :class:`~pyfsr.models.RunNode` (``pk``/``name``/``status`` +
        nested ``children``), walking ``parent_wf`` links down to ``depth``. This
        encodes the trigger→run→child linkage so you don't have to locate a
        parent/child by name in the raw ``/api/wf/api/workflows`` listing.

        A ``task_id`` from :meth:`trigger` resolves to the run it directly started;
        any sub-playbooks that run launches (``workflow_reference`` /
        ``apply_async`` loop children, linked by ``parent_wf``) appear as
        ``children``. Synchronous references whose children the platform links by
        ``parent_wf`` are included too.

        Args:
            run: a run pk / ``@id`` path / ``task_id`` (same forms as
                :meth:`child_runs`).
            depth: how many generations to descend (default 3; ``1`` = the run and
                its immediate children only, ``0`` = just the run).
            limit: max children fetched per node (default 100).

        Returns:
            the root :class:`~pyfsr.models.RunNode`. ``pk`` is ``None`` if ``run``
            can't be resolved (the node still carries the original ``task_id``).
        """
        pk = self._resolve_run_pk(run)
        task_id = str(run) if _looks_like_uuid(str(run)) else None
        if pk is None:
            return RunNode(pk=None, task_id=task_id)
        try:
            summary = self.get_execution(str(pk))
            name, status = summary.get("name"), summary.get("status")
        except Exception:  # noqa: BLE001 - a missing run still yields a node
            name = status = None
        node = RunNode(pk=str(pk), name=name, status=status, task_id=task_id)
        if depth > 0:
            for child in self.child_runs(pk, limit=limit):
                child_pk = child.get("pk")
                if child_pk:
                    node.children.append(self.run_tree(child_pk, depth=depth - 1, limit=limit))
        return node

    def diagnose_run(
        self,
        playbook: str | None = None,
        *,
        playbook_uuid: str | None = None,
        run: str | int | None = None,
    ) -> dict[str, Any]:
        """Diff a playbook's **definition** (step graph) against a **run** (executed steps).

        Answers the agent question "did my playbook run do what I defined?" without
        making the agent cross-reference :meth:`get_definition`, :meth:`run_env`, and
        :meth:`why_failed` by hand. Resolves the playbook, fetches its step graph,
        resolves a run (the latest for the playbook, or the ``run`` pk/``@id``/
        ``task_id`` you pass), and returns a structured comparison:

          - each **defined step** with the status the run recorded for it (or
            ``None`` if the engine never reached it),
          - steps the run recorded that the current definition doesn't have
            (drift, e.g. after a re-publish changed the graph),
          - the run's overall status + the first failing step + its error,
          - a one-word ``verdict`` (``completed``/``failed``/``running``/
            ``no_run``/``no_definition``) and a one-line ``summary``.

        Step names are matched by display name (how :meth:`run_env` keys them); a
        step that appears in the definition but not in the run either was skipped
        by a decision branch (normal) or never reached because the run stopped
        early (the ``failing_step`` then names where it stopped). This method
        reports the facts; the agent judges whether the reached/skipped set
        matches intent.

        Args:
            playbook: the playbook name — resolved to uuid internally.
            playbook_uuid: the playbook uuid (use instead of ``playbook`` when you
                already have it).
            run: a run pk / ``@id`` path / ``task_id`` (resolved like
                :meth:`run_env`). When ``None`` (default), uses the most recent run
                for the playbook.

        Returns:
            A structured dict (see above). ``verdict`` is ``"no_definition"`` if
            the playbook can't be resolved, ``"no_run"`` if it has no executions.
        """
        # --- resolve the playbook definition --------------------------------
        if not playbook_uuid and playbook:
            playbook_uuid = self._resolve_uuid(playbook)
        if not playbook_uuid:
            return {
                "verdict": "no_definition",
                "summary": f"playbook {playbook!r} not found",
                "playbook": {"name": playbook, "uuid": None},
                "definition_steps": [],
                "executed_not_defined": [],
                "not_reached": [],
                "run": None,
                "failing_step": None,
                "error_message": None,
            }
        try:
            wf = self.get_definition(playbook_uuid, relationships=True)
        except Exception as exc:  # noqa: BLE001 - surface a clean verdict, not a crash
            return {
                "verdict": "no_definition",
                "summary": f"could not fetch definition for {playbook_uuid!r}: {exc}",
                "playbook": {"name": playbook, "uuid": playbook_uuid},
                "definition_steps": [],
                "executed_not_defined": [],
                "not_reached": [],
                "run": None,
                "failing_step": None,
                "error_message": None,
            }
        # Imported lazily to avoid a circular import: playbook_match imports
        # STEP_TYPE_NAMES/TRIGGER_TYPE_NAMES from this module.
        from ..playbook_match import parse_playbook

        parsed = parse_playbook(dict(wf))
        defined: list[dict[str, Any]] = []
        defined_names: set[str] = set()
        for s in parsed.steps:
            nm = s.name
            if nm:
                defined_names.add(nm)
            defined.append({"name": nm, "step_type": s.step_type_raw})

        # --- resolve the run -------------------------------------------------
        run_meta: dict[str, Any] | None = None
        run_pk: str | None
        if run is not None:
            run_pk = self._resolve_run_pk(run)
        else:
            latest = self.last_run(playbook=playbook, playbook_uuid=playbook_uuid)
            run_pk = (latest.get("pk") if latest else None) or (
                (latest.get("@id") or "").rstrip("/").rsplit("/", 1)[-1] if latest else None
            )
            if run_pk and latest:
                # Carry the summary fields the latest-run fetch already gave us.
                run_meta = {
                    "pk": str(run_pk),
                    "name": latest.get("name"),
                    "status": latest.get("status"),
                    "modified": latest.get("modified"),
                }
        if not run_pk:
            return {
                "verdict": "no_run",
                "summary": f"no executions found for playbook {parsed.name!r}",
                "playbook": {"name": parsed.name, "uuid": playbook_uuid},
                "definition_steps": defined,
                "executed_not_defined": [],
                "not_reached": [d["name"] for d in defined if d["name"]],
                "run": None,
                "failing_step": None,
                "error_message": None,
            }

        # --- fetch the run's executed steps + failure detail -----------------
        env = self.run_env(str(run_pk))
        executed = env.steps or {}
        run_status = (env.status or "").lower() or None
        if run_meta is None:
            run_meta = {"pk": str(run_pk), "name": parsed.name, "status": run_status, "modified": None}
        else:
            # Prefer the authoritative status from the full execution record.
            run_meta["status"] = run_status

        # Per-defined-step: did the run record an outcome for it, and what?
        not_reached: list[str] = []
        for d in defined:
            nm = d["name"]
            st = executed[nm].status if nm and nm in executed else None
            d["status"] = st
            # A step that never got an outcome (None) or was left pending/incipient
            # was not evaluated by the engine.
            if st is None or (isinstance(st, str) and st.lower() in {"incipient", "pending"}):
                if nm:
                    not_reached.append(nm)

        # Steps the run recorded that the current definition lacks (drift).
        executed_not_defined = [
            {"name": nm, "status": step.status} for nm, step in executed.items() if nm not in defined_names
        ]

        # First actual failing step + its error (reuse the why_failed selection).
        failing_step: str | None = None
        error_message: str | None = None
        for nm, step in executed.items():
            st = (step.status or "").lower()
            if st in _STEP_FAILURE_STATUSES:
                failing_step = nm
                res = step.result
                if isinstance(res, dict):
                    error_message = (
                        res.get("Error message") or res.get("errorMessage") or res.get("message") or res.get("error")
                    )
                elif res is not None:
                    error_message = str(res)
                break

        # --- verdict ---------------------------------------------------------
        if run_status in _STEP_FAILURE_STATUSES:
            verdict = "failed"
        elif run_status in {"running", "queued", "pending"}:
            verdict = "running"
        elif run_status in {"finished", "success"}:
            verdict = "completed"
        else:
            verdict = run_status or "unknown"

        reached = len(defined) - len(not_reached)
        summary = f"{parsed.name}: run {run_pk} {verdict} ({reached}/{len(defined)} defined steps reached"
        if failing_step:
            summary += f"; failed at {failing_step!r}"
        summary += ")"

        return {
            "verdict": verdict,
            "summary": summary,
            "playbook": {"name": parsed.name, "uuid": playbook_uuid},
            "run": run_meta,
            "definition_steps": defined,
            "executed_not_defined": executed_not_defined,
            "not_reached": not_reached,
            "failing_step": failing_step,
            "error_message": error_message,
        }

    # --------------------------------------------------------------- trigger
    def trigger(
        self,
        playbook: str,
        *,
        records: list[str] | str | None = None,
        inputs: dict[str, Any] | None = None,
        env: dict[str, Any] | None = None,
        follow: bool = False,
        timeout: float = 300,
        interval: float = 3,
    ) -> TriggerResponse | RunSummary:
        """Manually trigger a playbook and return its run handle.

        POSTs to ``/api/triggers/1/notrigger/<playbook_uuid>`` — the route the
        FortiSOAR UI uses for the *Execute* button on a manual-trigger playbook —
        and returns ``{"task_id": ...}`` for the started run. The ``task_id`` is a
        **query-only** key: pass it to :meth:`get_execution` / :meth:`wait` (which
        resolve it server-side via ``log_list(task_id=...)``) to track the run. Note
        the run *log records do not echo* ``task_id`` — it is a filter parameter, not
        a field on the returned run, so matching it against a listed run's fields will
        not work; query *by* it instead.

        Args:
            playbook: the playbook to run — a uuid, or a name resolved to its
                uuid (the playbook must have a *manual* trigger step).
            records: record IRI(s) to pass in as the trigger's selected records
                (e.g. ``"/api/3/alerts/<uuid>"`` or a list). A bare uuid/ref is
                expanded to an ``/api/3/alerts/`` IRI.
            inputs: values for the playbook's manual input fields, sent as the
                request body's ``inputs``.
            env: extra keys merged into the POST body verbatim, for the rare
                playbook expecting a custom trigger envelope.
            follow: if ``True``, block until the run completes and return the
                shaped run dict instead of the raw trigger response. Equivalent
                to calling :meth:`wait` on the returned ``task_id``.
            timeout: seconds to wait when ``follow=True`` (default 300).
            interval: poll interval in seconds when ``follow=True`` (default 3).

        Returns:
            When ``follow=False`` (default): the trigger response, typically
            ``{"task_id": "<run-uuid>"}``. When ``follow=True``: the shaped run
            dict after completion (same shape as :meth:`wait`).

        See also:
            :meth:`run_tree` resolves the returned ``task_id`` to the run **plus
            its child runs** (so you don't find a referenced child by name);
            :meth:`step_status` asserts a step finished without relying on
            ``set_variable`` values (only recorded with debug logging on).
        """
        uuid = playbook if _looks_like_uuid(playbook) else self._resolve_uuid(playbook)
        if not uuid:
            raise ValueError(f"playbook {playbook!r} not found")
        req = _build(TriggerRequest, "trigger", records=records, inputs=inputs, env=env or {})
        resp = self.client.post(f"/api/triggers/1/notrigger/{uuid}", data=req.to_body())
        if follow:
            task_id = resp.get("task_id") if isinstance(resp, dict) else None
            if not task_id:
                raise ValueError(f"trigger response missing task_id: {resp!r}")
            return self.wait(task_id, timeout=timeout, interval=interval)
        return TriggerResponse(**resp) if isinstance(resp, dict) else resp

    # ------------------------------------------------------------------ wait
    def wait(
        self,
        task_id: str,
        *,
        timeout: float = 300,
        interval: float = 3,
    ) -> RunSummary:
        """Poll until a run reaches a terminal status and return the shaped run dict.

        Uses :meth:`log_list` (keyed by ``task_id``) rather than the heavier
        per-run fetch, so it works even before the run appears in the live table.
        Raises :exc:`TimeoutError` if ``timeout`` seconds elapse before completion.

        Args:
            task_id: the ``task_id`` returned by :meth:`trigger` or
                :meth:`trigger_by_name`.
            timeout: seconds to wait before raising (default 300).
            interval: seconds between polls (default 3).

        Returns:
            A shaped run dict (``{task_id, name, status, error_message, ...}``).
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("wait() requires a non-empty task_id")
        deadline = time.monotonic() + timeout
        while True:
            resp = self.log_list(task_id=task_id, limit=1)
            members = (resp or {}).get("hydra:member") or []
            if members:
                run = members[0]
                status = (run.get("status") or "").lower()
                if status in _TERMINAL_STATUSES:
                    return _shape_run(run)
            if time.monotonic() >= deadline:
                raise TimeoutError(f"playbook run {task_id!r} did not finish within {timeout}s")
            time.sleep(interval)

    def wait_for_run(
        self,
        playbook: str | None = None,
        *,
        playbook_uuid: str | None = None,
        since: str | float | None = None,
        timeout: float = 120,
        poll_interval: float = 3,
    ) -> RunSummary:
        """Poll until the most recent run of a playbook reaches a terminal status.

        Queries :meth:`execution_history` (merged from live + historical tables,
        newest first) and polls until the most recent run *newer than* ``since``
        reaches a terminal state (status in finished/failed/error/cancelled/aborted).

        This is useful for verifying that a triggered playbook has completed —
        more ergonomic than manually tracking ``task_id`` when the playbook's
        definition (by name) is the natural reference.

        Args:
            playbook: the playbook name — resolved to uuid internally.
            playbook_uuid: the playbook uuid (use instead of ``playbook`` when
                you already have it).
            since: an optional timestamp (ISO string or UNIX float) — only polls
                runs *newer* than this. Useful when the playbook may have old
                runs already in a terminal state. Default ``None`` (polls the
                absolute newest run, which is often still running).
            timeout: seconds to wait before raising :exc:`TimeoutError`
                (default 120).
            poll_interval: seconds between polls (default 3).

        Returns:
            A shaped run dict (``{task_id, name, status, error_message, modified,
            uuid, pk, source}``) for the completed run.

        Raises:
            TimeoutError: if no terminal run newer than ``since`` is found within
                ``timeout`` seconds.
            ValueError: if the playbook does not exist or is not found.

        Example:
            >>> # Trigger a playbook, then wait for it to finish
            >>> result = client.playbooks.trigger("AI Investigation", records=[alert_uuid])
            >>> task_id = result["task_id"]
            >>> # ... or just wait by playbook name (polls the newest run)
            >>> run = client.playbooks.wait_for_run(playbook="AI Investigation", timeout=120)
            >>> print(f"Run {run['pk']}: {run['status']}")
            >>> if run["error_message"]:
            ...     print(f"Error: {run['error_message']}")
        """
        # Resolve playbook name to uuid if needed
        uuid = playbook_uuid or (self._resolve_uuid(playbook) if playbook else None)
        if not uuid:
            raise ValueError(f"playbook {playbook!r} not found")

        deadline = time.monotonic() + timeout
        while True:
            # Fetch the most recent run(s)
            runs = self.execution_history(playbook_uuid=uuid, limit=1)
            if not runs:
                # No runs found yet; keep polling
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"playbook {playbook or uuid!r} has no runs after {since!r} within {timeout}s")
                time.sleep(poll_interval)
                continue

            run = runs[0]

            # Check if this run is newer than 'since', if given
            if since is not None:
                # Parse since as ISO string or UNIX float
                if isinstance(since, str):
                    # ISO string comparison (modified is ISO)
                    since_str = since
                else:
                    # Convert UNIX float to ISO string for comparison
                    import datetime

                    since_dt = datetime.datetime.fromtimestamp(since, tz=datetime.timezone.utc)
                    since_str = since_dt.isoformat()

                run_modified = run.get("modified", "")
                if run_modified < since_str:
                    # This run is older than 'since'; keep polling for a newer one
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"playbook {playbook or uuid!r} has no runs newer than {since!r} within {timeout}s"
                        )
                    time.sleep(poll_interval)
                    continue

            # Check if this run is in a terminal state
            status = (run.get("status") or "").lower()
            if status in _TERMINAL_STATUSES:
                return run

            # Not terminal yet; keep polling
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"playbook {playbook or uuid!r} run {run.get('pk') or run.get('uuid')!r} "
                    f"did not finish within {timeout}s (currently {status!r})"
                )
            time.sleep(poll_interval)

    # ---------------------------------------------------------------- resume
    def resume(
        self,
        run_pk: str,
        *,
        manual_input_id: int,
        input: Any = None,
        step_iri: str | None = None,
        step_id: str | None = None,
        approved: bool | None = None,
    ) -> dict[str, Any]:
        """Resume a run waiting on manual input / approval.

        POSTs to ``/api/wf/api/workflows/<pk>/wfinput_resume/``. ``input`` is the
        manual-input value payload; ``approved`` (when set) drives an approval
        step. The other args identify which waiting step to resume.
        """
        if not isinstance(run_pk, str) or not run_pk.strip():
            raise ValueError("resume() requires a non-empty run pk")
        req = _build(
            ResumeRequest,
            "resume",
            input=input,
            step_iri=step_iri,
            step_id=step_id,
            manual_input_id=manual_input_id,
            approved=approved,
        )
        return self.client.post(
            f"/api/wf/api/workflows/{run_pk.strip()}/wfinput_resume/?format=json",
            data=req.to_body(),
        )

    # ------------------------------------------------------- run control verbs
    def start(self, run_pk: str) -> dict[str, Any]:
        """Manually queue a workflow run (``POST .../workflows/{pk}/start/``)."""
        return self.client.post(f"/api/wf/api/workflows/{_pk(run_pk)}/start/", data={})

    def retry(self, run_pk: str) -> dict[str, Any]:
        """Retry a failed run from its failed step (``POST .../workflows/{pk}/retry/``)."""
        return self.client.post(f"/api/wf/api/workflows/{_pk(run_pk)}/retry/", data={})

    def approval(
        self,
        run_pk: str,
        *,
        decision: str = "approve",
        comment: str | None = None,
        user: str | None = None,
    ) -> ManualInputResume:
        """Drive an approval / manual-input gate on a paused run to a decision.

        Resolves the run's pending manual-wf-input (the modern, resumable
        approval gate -- a ``manual_input`` step, optionally with
        ``is_approval: true``), picks the response option matching ``decision``,
        and resumes via ``wfinput_resume`` -- the canonical resume path the
        FortiSOAR UI uses (per ``app.unmin.js`` + ``data/MANUAL_INPUT.md``).

        ``decision`` maps to a response option: ``"approve"`` (default) selects
        the primary option; ``"reject"`` selects the first non-primary option;
        any other string is matched against an option label case-insensitively.

        **Legacy ``type: approval`` (ApprovalManualInput) is NOT resumable here.**
        That step creates an ``approvals``-module record but NO
        ``manual-wf-input``, so ``wfinput_resume`` has no ``manual_input_id`` to
        target (the old ``POST .../approval/`` endpoint only *lists* approvals,
        it does not resume them). Author the gate as a ``manual_input`` step
        (optionally ``is_approval: true``) instead. This raises :class:`ValueError`
        with that guidance when the run has no pending manual-wf-input.

        Args:
            run_pk: the paused run's primary key (the trailing segment of its
                ``@id`` -- what :meth:`get_execution` takes).
            decision: ``"approve"`` (default), ``"reject"``, or an option label.
            comment: advisory note (not currently sent on the wire for the modern
                resume; reserved for approval-audit use).
            user: submitting user IRI (``/api/3/people/<uuid>``); auto-resolved
                to an admin when omitted.

        Returns:
            The :class:`~pyfsr.models.ManualInputResume` ack (``task_id`` +
            ``message``); the resume is asynchronous.

        Raises:
            ValueError: the run has no pending manual-wf-input (it already
                finished, or it is paused on a legacy ``type: approval`` step).
            LookupError: ``decision`` does not match any response option.
        """
        mi_api = self.client.manual_input
        resp = self.client.get(
            "/api/wf/api/manual-wf-input/",
            params={"workflow": _pk(run_pk), "format": "json", "ordering": "-id"},
        )
        rows = resp.get("hydra:member", []) if isinstance(resp, dict) else (resp if isinstance(resp, list) else [])
        if not rows:
            raise ValueError(
                f"run {run_pk!r} has no pending manual-input/approval to resume. If it "
                f"is paused on a legacy `type: approval` step, that step is NOT "
                f"programmatically resumable (it creates an approvals-module record, "
                f"not a manual-wf-input). Author the gate as a `manual_input` step "
                f"(optionally `is_approval: true`) instead."
            )
        input_id = int(rows[0]["id"])
        user_iri = user or mi_api._resolve_user_iri()
        full = mi_api.retrieve(input_id, owners=user_iri)
        options = (full.response_mapping or {}).get("options") or []
        opt = _pick_approval_option(options, decision)
        return mi_api.resume(
            full.workflow,
            step_iri=opt["step_iri"],
            step_id=full.step_id,
            manual_input_id=input_id,
            user=user_iri,
            input={},
        )

    def count(self, *, logs: str = "all") -> dict[str, Any]:
        """Total run count (``GET .../workflows/count/``).

        ``logs`` is ``"all"`` (recent + historical, default), ``"recent"``, or
        ``"historical"``. The trailing slash matters (the slashless path 403s).
        """
        return self.client.get("/api/wf/api/workflows/count/", params={"logs": logs})

    def log_list(
        self,
        *,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 30,
        **filters: Any,
    ) -> dict[str, Any]:
        """Status lookup for executing playbooks (``POST .../workflows/log_list/``).

        Primarily keyed by ``task_id`` (what :meth:`trigger`/:meth:`trigger_by_name`
        return). Other query filters (``status``, ``template_iri``, ``records``,
        ``created_after``, ``tags_include``, …) pass through verbatim as
        ``filters``; ``limit`` caps the page.
        """
        params: dict[str, Any] = {"limit": limit, **filters}
        if task_id is not None:
            params["task_id"] = task_id
        if status is not None:
            params["status"] = status
        return self.client.post("/api/wf/api/workflows/log_list/", data={}, params=params)

    def query_logs(
        self,
        *,
        filters: list[dict[str, Any]] | None = None,
        logic: str = "AND",
        limit: int | None = None,
        sort: list[dict[str, Any]] | None = None,
        aggregates: list[dict[str, Any]] | None = None,
        logs: str = "all",
    ) -> dict[str, Any]:
        """Query the playbook log store by body filter (``POST .../query/workflow_logs/``).

        ``filters`` is a list of filter dicts combined by ``logic`` (``"AND"``/
        ``"OR"``); ``sort``/``aggregates`` follow the engine's query shape. ``logs``
        restricts the source (``"all"``/``"recent"``/``"historical"``).
        """
        body: dict[str, Any] = {"logic": logic}
        if filters is not None:
            body["filters"] = filters
        if sort is not None:
            body["sort"] = sort
        if aggregates is not None:
            body["aggregates"] = aggregates
        if limit is not None:
            body["limit"] = limit
        return self.client.post("/api/wf/api/query/workflow_logs/", data=body, params={"logs": logs})

    # ----------------------------------------------------------- manual inputs
    def manual_inputs(self) -> list[dict[str, Any]]:
        """List runs awaiting manual input (``POST .../manual-wf-input/list_wfinput/``).

        Each entry carries ``id`` (the ``manual_input_id`` for :meth:`resume`) and
        ``step_id``. Buttons/options are omitted here — fetch them per-record with
        :meth:`retrieve_manual_input`. (POST-only; GET 405s.)
        """
        return extract_members(self.client.post("/api/wf/api/manual-wf-input/list_wfinput/", data={}))

    def retrieve_manual_input(self, pk: str) -> dict[str, Any]:
        """Fetch one manual-input record with its buttons/options.

        ``POST .../manual-wf-input/{pk}/retrieve_wfinput/``. Returns the full
        record including ``response_mapping.options[]`` (each option's label and
        ``step_iri``) — which :meth:`manual_inputs` omits, so you need this to know
        what to send when resuming via :meth:`resume`.
        """
        return self.client.post(f"/api/wf/api/manual-wf-input/{_pk(pk)}/retrieve_wfinput/", data={})

    def update_manual_input(self, pk: str, **fields: Any) -> dict[str, Any]:
        """Update a manual-input record (``PUT .../manual-wf-input/{pk}/``).

        .. warning::
            This updates the record but does **not** advance an ``awaiting`` run.
            To actually resume, use :meth:`resume`.
        """
        return self.client.put(f"/api/wf/api/manual-wf-input/{_pk(pk)}/", data=fields)

    # --------------------------------------------------------- named triggers
    def trigger_by_name(
        self,
        name: str,
        *,
        body: dict[str, Any] | None = None,
        deferred: bool = False,
        raise_on_status: bool = True,
    ) -> TriggerResponse | Any:
        """Fire a playbook by its trigger's endpoint name.

        ``POST /api/triggers/1/{name}`` (or ``/api/triggers/1/deferred/{name}``
        when ``deferred=True``, which always 202s and runs on a worker). This is
        the named-webhook trigger route — distinct from :meth:`trigger`, which
        uses the manual-execute (``notrigger``) route by playbook uuid. Returns
        the trigger response (typically ``{"task_id": ...}``).

        With ``raise_on_status=False`` returns the raw
        :class:`requests.Response` (``.status_code`` / ``.json()``) instead of a
        :class:`~pyfsr.models.TriggerResponse` — for access-control probes that need to
        distinguish a permit (200) from a denial (401/403) without catching.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("trigger_by_name() requires a non-empty name")
        prefix = "/api/triggers/1/deferred/" if deferred else "/api/triggers/1/"
        resp = self.client.post(
            f"{prefix}{name.strip('/ ')}",
            data=body or {},
            raise_on_status=raise_on_status,
        )
        if not raise_on_status:
            return resp
        return TriggerResponse(**resp) if isinstance(resp, dict) else resp

    def trigger_action(
        self,
        route_uuid: str,
        *,
        module: str,
        record_uuid: str,
        playbook_uuid: str | None = None,
        env: dict[str, Any] | None = None,
    ) -> TriggerResponse | Any:
        """Fire a record-context action trigger (``POST /api/triggers/1/action/{route_uuid}``).

        This is the route FortiSOAR uses for playbooks with a *record-action*
        (``cybersponse.action``) trigger step — distinct from :meth:`trigger`, which
        uses the manual-execute (``notrigger``) route. The ``route_uuid`` is the
        trigger step's ``route`` field on the playbook definition; retrieve it via
        ``client.playbooks.get_definition(uuid, relationships=True)`` and look under
        ``triggerStep.arguments.route``.

        Args:
            route_uuid: the trigger step's route uuid.
            module: the module name (e.g. ``"alerts"``).
            record_uuid: uuid of the record to run against.
            playbook_uuid: if provided, added to the body as ``__uuid`` (the playbook
                being triggered). Some appliance versions require it.
            env: extra keys merged into the POST body verbatim.

        Returns:
            The trigger response, typically ``{"task_id": "<run-uuid>"}``.
        """
        if not isinstance(route_uuid, str) or not route_uuid.strip():
            raise ValueError("trigger_action() requires a non-empty route_uuid")
        req = _build(
            TriggerActionRequest,
            "trigger_action",
            module=module,
            record_uuid=record_uuid,
            playbook_uuid=playbook_uuid,
            env=env or {},
        )
        resp = self.client.post(f"/api/triggers/1/action/{route_uuid.strip()}", data=req.to_body())
        return TriggerResponse(**resp) if isinstance(resp, dict) else resp

    # --------------------------------------------------------- step diagnostics
    def historical_steps(
        self,
        task_id: str,
        *,
        limit: int = 200,
        status: str | None = None,
        name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch per-step execution records for a run from ``/api/wf/api/historical-steps/``.

        Keyed by ``task_id`` (what :meth:`trigger` returns). Steps are ordered
        by creation time (oldest first). This endpoint only populates after a run
        reaches a terminal state — on a live run, check :meth:`get` with
        ``step_detail=True`` instead.

        Supported filters beyond ``task_id``: ``status``, ``name`` (step display
        name), ``func`` (step function key). Pass additional filters as keyword
        args via the ``name``/``status`` params or extend with ``**params`` if
        you need others.

        Returns the ``hydra:member`` list; each item carries ``name``, ``status``,
        ``func``, ``result``, ``input``, ``created``, ``modified``.
        """
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("historical_steps() requires a non-empty task_id")
        params: dict[str, Any] = {
            "task_id": task_id.strip(),
            "format": "json",
            "limit": limit,
            "ordering": "created",
        }
        if status is not None:
            params["status"] = status
        if name is not None:
            params["name"] = name
        resp = self.client.get("/api/wf/api/historical-steps/", params=params)
        return extract_members(resp)

    def render_jinja(
        self,
        template: str,
        values: dict[str, Any] | None = None,
    ) -> str:
        """Render a Jinja2 template against a context via ``POST /api/wf/api/jinja-editor/``.

        Sends ``template`` + ``values`` (the Jinja context, typically a run's
        ``env`` dict) to FortiSOAR's built-in Jinja editor endpoint and returns
        the rendered string. Useful for testing playbook Jinja expressions against
        real run data without triggering a full run.

        Args:
            template: the Jinja2 template string to render.
            values: the context dict (e.g. from :meth:`run_env`'s ``"env"`` key).

        Returns:
            The rendered output as a string.
        """
        resp = self.client.post(
            "/api/wf/api/jinja-editor/",
            data={"template": template, "values": values or {}},
        )
        if isinstance(resp, dict):
            out = resp.get("result") or resp.get("output") or resp.get("rendered") or resp.get("value")
            if out is not None:
                return str(out)
            import json as _json

            return _json.dumps(resp, indent=2, default=str)
        return str(resp)
