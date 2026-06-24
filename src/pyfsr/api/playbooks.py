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
from typing import Any

from ..pagination import HydraPage, extract_members
from ..projection import project
from ..query import Query
from .base import BaseAPI

_TERMINAL_STATUSES = frozenset({"finished", "failed", "error", "cancelled", "aborted"})

_RUN_PATHS = ("/api/wf/api/workflows/", "/api/wf/api/historical-workflows/")
# Playbook *definitions* (the templates), distinct from the run-history tables above.
_WORKFLOWS = "/api/3/workflows"
_WORKFLOWS_BULKUPSERT = "/api/3/bulkupsert/workflows"
# A hard delete must also reach already-recycled rows; together they skip the recycle bin.
_HARD_DELETE = {"$hardDelete": "true", "$showDeleted": "true"}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


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


def _alert_iri(ref: str) -> str:
    """Expand a bare alert uuid/ref to a full ``/api/3/alerts/<uuid>`` IRI."""
    if ref.startswith("/api/"):
        return ref
    return f"/api/3/alerts/{ref.rstrip('/').split('/')[-1].split(':')[-1]}"


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


def _prepare_clone_body(src: dict[str, Any], *, new_name: str, is_active: bool) -> dict[str, Any]:
    """Build the POST body for a playbook clone from a source definition.

    Regenerates every owned UUID and rewrites all references in one pass by
    substituting old→new UUID strings over the serialized definition. Because
    UUIDs are globally-unique 36-char tokens, a plain string replacement safely
    catches both bare references (route ``sourceStep``/``targetStep``) and ones
    embedded in IRIs (``triggerStep``, step ``group``, nested ``@id``s) without
    needing to know every field that can hold one.
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
    body["name"] = new_name
    body["aliasName"] = None  # the alias (#Name anchor) must be unique; let it re-derive
    body["isActive"] = is_active
    return body


def _shape_run(m: dict[str, Any]) -> dict[str, Any]:
    """Flatten a raw workflow-run record to the fields callers usually want."""
    res = m.get("result") if isinstance(m.get("result"), dict) else {}
    err = None
    if isinstance(res, dict):
        err = res.get("Error message") or res.get("error") or res.get("message")
    pk_url = m.get("@id") or ""
    pk = pk_url.rstrip("/").rsplit("/", 1)[-1] if pk_url else None
    return {
        "task_id": m.get("task_id"),
        "name": m.get("name"),
        "status": m.get("status"),
        "error_message": err,
        "modified": m.get("modified"),
        "uuid": m.get("uuid"),
        "pk": pk,
        "source": m.get("_source"),  # "live" or "historical"
    }


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

    def get_definition(
        self,
        uuid: str,
        *,
        relationships: bool = True,
        raw: bool = False,
        typed: bool = False,
    ) -> dict[str, Any]:
        """Fetch one playbook definition by uuid (``GET /api/3/workflows/{uuid}``).

        ``relationships=True`` (default) inlines the workflow's steps/routes/groups, which is
        the usual shape callers want when inspecting or cloning a playbook.
        """
        uuid = _require_uuid(uuid, "get_definition")
        params = {"$relationships": "true"} if relationships else None
        resp = self.client.get(f"{_WORKFLOWS}/{uuid}", params=params)
        if typed:
            from ..models import Workflow

            return Workflow(**resp)
        return resp if raw else resp

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
        if not isinstance(name, str) or not name.strip():
            raise ValueError("create_playbook() requires a non-empty name")
        if not isinstance(collection, str) or not collection.strip():
            raise ValueError("create_playbook() requires a collection uuid or IRI")
        coll_iri = collection if collection.startswith("/api/") else f"/api/3/workflow_collections/{collection}"
        body: dict[str, Any] = {
            "name": name.strip(),
            "collection": coll_iri,
            "isActive": is_active,
            "remoteExecutableFlag": remote_executable,
            **fields,
        }
        if priority is not None:
            body["priority"] = priority
        if origin is not None:
            body["playbookOrigin"] = origin
        return self.client.post(_WORKFLOWS, data=body)

    def clone(
        self,
        uuid: str,
        new_name: str,
        *,
        collection: str | None = None,
        is_active: bool = False,
    ) -> dict[str, Any]:
        """Clone an existing playbook definition under a new name.

        Fetches the source playbook with its steps/routes/groups inlined, then
        **remaps every UUID** (the workflow itself plus each step, route, and
        group) to a fresh one, rewiring all the internal references — route
        ``sourceStep``/``targetStep``, the workflow's ``triggerStep``, and each
        step's ``group`` — so the copy is fully self-contained and never collides
        with the original. Server-managed fields (``@id``/``id``/create+modify
        stamps, ``deletedAt``, ``versions``) are dropped. POSTs the result to
        ``/api/3/workflows``.

        Args:
            uuid: source playbook uuid.
            new_name: display name for the clone (required — a clone must be
                distinguishable from its source).
            collection: optionally re-home the clone into a different collection
                (uuid or IRI). Defaults to the source's collection.
            is_active: whether the clone is active. Defaults to ``False`` so a
                copy never starts firing on triggers before it's been reviewed.

        Returns:
            The created clone's playbook definition record.
        """
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("clone() requires a non-empty new_name")
        uuid = _require_uuid(uuid, "clone")
        src = self.get_definition(uuid, relationships=True)

        body = _prepare_clone_body(src, new_name=new_name.strip(), is_active=is_active)
        if collection is not None:
            if not isinstance(collection, str) or not collection.strip():
                raise ValueError("clone() collection must be a uuid or IRI")
            body["collection"] = (
                collection if collection.startswith("/api/") else f"/api/3/workflow_collections/{collection}"
            )
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
        typed: bool = False,
        fields: list[str] | tuple[str, ...] | None = None,
        summary: bool = False,
        show_deleted: bool = False,
    ) -> Any:
        """Run a structured query against ``/api/query/workflows``.

        This mirrors :meth:`pyfsr.records.RecordSet.query` for the workflow-definition
        surface, so callers can use the same body-filter shapes the framework probes with.
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
        if typed:
            from ..models import Workflow

            page_obj.members = [Workflow(**m) if isinstance(m, dict) else m for m in page_obj.members]
            if raw:
                return page_obj
            return page_obj.members
        if fields or summary:
            return project(page_obj, fields=fields, summary=summary)
        return page_obj if raw else page_obj.members

    def _fetch_runs_both(self, *, limit: int, extra_qs: str = "") -> list[dict[str, Any]]:
        """Fetch + merge ``/workflows/`` and ``/historical-workflows/``."""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in _RUN_PATHS:
            qs = f"?format=json&limit={limit}&ordering=-modified&parent_wf__isnull=True{extra_qs}"
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
        raw: bool = False,
        typed: bool = False,
    ) -> list[dict[str, Any]]:
        """List recent playbook executions, newest first (live + historical merged).

        Scope to one playbook by ``playbook`` (name, resolved to uuid) or
        ``playbook_uuid``. Returns shaped dicts
        (``{task_id, name, status, error_message, modified, uuid, pk, source}``)
        by default; pass ``raw=True`` for the full unshaped run records, or
        ``typed=True`` for ``WorkflowRun`` objects (dict-compatible). ``typed``
        wins over ``raw``.
        """
        extra = ""
        if playbook_uuid or playbook:
            if not playbook_uuid:
                playbook_uuid = self._resolve_uuid(playbook)
                if not playbook_uuid:
                    return []
            extra = f"&template_iri=/api/3/workflows/{playbook_uuid}"
        members = self._fetch_runs_both(limit=limit, extra_qs=extra)[:limit]
        if typed:
            from ..models import WorkflowRun

            return [WorkflowRun(**m) for m in members]
        return members if raw else [_shape_run(m) for m in members]

    def get_execution(
        self,
        run_pk: str,
        *,
        raw: bool = False,
        typed: bool = False,
        step_detail: bool = False,
    ) -> dict[str, Any]:
        """Fetch one playbook execution by its pk.

        The pk is the trailing segment of a run's ``@id`` URL. Tries the live
        table first, then historical. Returns a shaped dict by default;
        ``raw=True`` for the full record, or ``typed=True`` for a
        ``WorkflowRun``. ``typed`` wins over ``raw``.

        Pass ``step_detail=True`` to include the per-step execution trace; the
        step results land under ``workflow``/``result``. ``step_detail`` implies
        ``raw`` unless ``typed`` is set.
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
                source = "historical" if "historical" in path else "live"
                resp["_source"] = source
                if typed:
                    from ..models import WorkflowRun

                    return WorkflowRun(**resp)
                if step_detail:
                    return resp
                return resp if raw else _shape_run(resp)
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
    ) -> list[dict[str, Any]]:
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
        raw: bool = False,
    ) -> dict[str, Any] | None:
        """Return the most recent run summary for a playbook.

        Fetches the most recent execution (merged from live + historical tables,
        newest first) for the specified playbook. The playbook may be identified
        by ``playbook`` (name, resolved to uuid) or ``playbook_uuid``.

        Args:
            playbook: the playbook name — resolved to uuid internally.
            playbook_uuid: the playbook uuid (use instead of ``playbook`` when
                you already have it).
            raw: if ``True``, returns the full unshaped run record; otherwise
                returns the shaped dict
                (``{task_id, name, status, error_message, modified, uuid, pk, source}``).

        Returns:
            The shaped (or raw) run dict for the most recent execution, or
            ``None`` if the playbook has no runs or does not exist.

        Example:
            >>> run = client.playbooks.last_run(playbook="Block IP")
            >>> if run:
            ...     print(f"{run['name']}: {run['status']} ({run['pk']})")
        """
        runs = self.execution_history(
            playbook=playbook,
            playbook_uuid=playbook_uuid,
            limit=1,
            raw=raw,
        )
        return runs[0] if runs else None

    def why_failed(
        self,
        playbook: str | None = None,
        *,
        playbook_uuid: str | None = None,
        raw: bool = False,
    ) -> dict[str, Any] | None:
        """Find the most recent run and fetch its error details.

        Locates the most recent execution for the specified playbook, then
        calls :meth:`get_execution` with ``step_detail=True`` to retrieve the
        full record (error_message and step results are only fully populated
        there). The playbook may be identified by ``playbook`` (name, resolved
        to uuid) or ``playbook_uuid``.

        Args:
            playbook: the playbook name — resolved to uuid internally.
            playbook_uuid: the playbook uuid (use instead of ``playbook`` when
                you already have it).
            raw: if ``True``, returns the full get_execution record with all
                step details; otherwise returns a slim projection
                (``{status, failing_step, error_message, pk}``).

        Returns:
            When ``raw=False`` (default): a dict with keys:

            - ``status``: the run's terminal status.
            - ``error_message``: the error message (populated from the run's
              result or the first failing step). ``None`` if the run succeeded.
            - ``failing_step``: the first step with a failure status
              (e.g. 'failed', 'errored'). ``None`` if the run succeeded.
            - ``pk``: the run's pk (the trailing segment of its @id URL).

            When ``raw=True``: the full run record from
            :meth:`get_execution` (includes ``steps[]`` with per-step
            execution traces, ``env``, etc.).

            Returns ``None`` if the playbook has no runs or does not exist.

        Example:
            >>> failure = client.playbooks.why_failed(playbook="Block IP")
            >>> if failure:
            ...     print(f"Run {failure['pk']}: {failure['status']}")
            ...     if failure['failing_step']:
            ...         print(f"  Failed at step: {failure['failing_step']}")
            ...     if failure['error_message']:
            ...         print(f"  Error: {failure['error_message']}")
        """
        run = self.last_run(
            playbook=playbook,
            playbook_uuid=playbook_uuid,
            raw=True,
        )
        if not run:
            return None

        pk = run.get("pk") or (run.get("@id") or "").rstrip("/").rsplit("/", 1)[-1]
        if not pk:
            return None

        full = self.get_execution(pk, step_detail=True)
        if raw:
            return full

        # Extract the first failing step, if any.
        failing_step = None
        failing_step_msg = None
        for step in full.get("steps") or []:
            if not isinstance(step, dict):
                continue
            status = (step.get("status") or "").lower()
            # Look for non-success statuses: failure, failed, error, errored, etc.
            if status and status not in ("finished", "success", "running"):
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

        return {
            "status": full.get("status"),
            "failing_step": failing_step,
            "error_message": error_message,
            "pk": pk,
        }

    def run_env(self, run_pk: str) -> dict[str, Any]:
        """Return a run's execution environment + per-step results.

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
        """
        full = self.get_execution(run_pk, step_detail=True)
        steps: dict[str, Any] = {}
        for s in full.get("steps") or []:
            if not isinstance(s, dict):
                continue
            name = s.get("name")
            if not name:
                md = s.get("metadata") or {}
                name = (md.get("metadata") or {}).get("name") or md.get("name")
            if not name:
                continue
            steps[name] = {"status": s.get("status"), "result": s.get("result")}
        return {
            "env": full.get("env") or {},
            "status": full.get("status"),
            "steps": steps,
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
    ) -> dict[str, Any]:
        """Manually trigger a playbook and return its run handle.

        POSTs to ``/api/triggers/1/notrigger/<playbook_uuid>`` — the route the
        FortiSOAR UI uses for the *Execute* button on a manual-trigger playbook —
        and returns ``{"task_id": ...}`` for the started run. Track it with
        :meth:`runs` / :meth:`get` (the ``task_id`` matches a run record's).

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
        """
        uuid = playbook if _looks_like_uuid(playbook) else self._resolve_uuid(playbook)
        if not uuid:
            raise ValueError(f"playbook {playbook!r} not found")
        body: dict[str, Any] = dict(env or {})
        if records is not None:
            refs = [records] if isinstance(records, str) else list(records)
            body["records"] = [_alert_iri(r) for r in refs]
        if inputs is not None:
            body["inputs"] = inputs
        resp = self.client.post(f"/api/triggers/1/notrigger/{uuid}", data=body)
        if follow:
            task_id = resp.get("task_id") if isinstance(resp, dict) else None
            if not task_id:
                raise ValueError(f"trigger response missing task_id: {resp!r}")
            return self.wait(task_id, timeout=timeout, interval=interval)
        return resp

    # ------------------------------------------------------------------ wait
    def wait(
        self,
        task_id: str,
        *,
        timeout: float = 300,
        interval: float = 3,
    ) -> dict[str, Any]:
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
        body: dict[str, Any] = {
            "input": input,
            "step_iri": step_iri,
            "step_id": step_id,
            "manual_input_id": int(manual_input_id),
        }
        if approved is not None:
            body["approved"] = bool(approved)
        return self.client.post(
            f"/api/wf/api/workflows/{run_pk.strip()}/wfinput_resume/?format=json",
            data=body,
        )

    # ------------------------------------------------------- run control verbs
    def start(self, run_pk: str) -> dict[str, Any]:
        """Manually queue a workflow run (``POST .../workflows/{pk}/start/``)."""
        return self.client.post(f"/api/wf/api/workflows/{_pk(run_pk)}/start/", data={})

    def retry(self, run_pk: str) -> dict[str, Any]:
        """Retry a failed run from its failed step (``POST .../workflows/{pk}/retry/``)."""
        return self.client.post(f"/api/wf/api/workflows/{_pk(run_pk)}/retry/", data={})

    def approval(self, run_pk: str, *, decision: str, comment: str | None = None) -> dict[str, Any]:
        """Drive an approval step (``POST .../workflows/{pk}/approval/``).

        ``decision`` is the approval choice (e.g. ``"approved"``/``"rejected"``);
        ``comment`` is an optional note. For input-style resumes use :meth:`resume`.
        """
        body: dict[str, Any] = {"decision": decision}
        if comment is not None:
            body["comment"] = comment
        return self.client.post(f"/api/wf/api/workflows/{_pk(run_pk)}/approval/", data=body)

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
        self, name: str, *, body: dict[str, Any] | None = None, deferred: bool = False
    ) -> dict[str, Any]:
        """Fire a playbook by its trigger's endpoint name.

        ``POST /api/triggers/1/{name}`` (or ``/api/triggers/1/deferred/{name}``
        when ``deferred=True``, which always 202s and runs on a worker). This is
        the named-webhook trigger route — distinct from :meth:`trigger`, which
        uses the manual-execute (``notrigger``) route by playbook uuid. Returns
        the trigger response (typically ``{"task_id": ...}``).
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("trigger_by_name() requires a non-empty name")
        prefix = "/api/triggers/1/deferred/" if deferred else "/api/triggers/1/"
        return self.client.post(f"{prefix}{name.strip('/ ')}", data=body or {})

    def trigger_action(
        self,
        route_uuid: str,
        *,
        module: str,
        record_uuid: str,
        playbook_uuid: str | None = None,
        env: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
        body: dict[str, Any] = dict(env or {})
        body["singleRecordExecution"] = True
        body["__resource"] = module
        body["records"] = [f"/api/3/{module}/{record_uuid}"]
        if playbook_uuid is not None:
            body["__uuid"] = playbook_uuid
        return self.client.post(f"/api/triggers/1/action/{route_uuid.strip()}", data=body)

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
