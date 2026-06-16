"""Playbook run history and manual-input resume.

Wraps FortiSOAR's workflow-run surface (``/api/wf/api``). Accessed as
``client.playbooks``.

Run history lives in two tables: ``/workflows/`` holds recent/live runs, but
FortiSOAR purges them to ``/historical-workflows/`` every ~30-60 min (the
historical table also carries richer inline fields). ``runs()`` queries both
and merges them, deduped by IRI and sorted newest-first, so you don't go blind
to older runs.

Example:
    >>> client.playbooks.runs(limit=10)                       # latest runs
    >>> client.playbooks.runs(playbook="Block IP", limit=5)   # one playbook
    >>> client.playbooks.get("<run-pk>")                       # one run, full
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .base import BaseAPI

_RUN_PATHS = ("/api/wf/api/workflows/", "/api/wf/api/historical-workflows/")

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _looks_like_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s.strip()))


def _alert_iri(ref: str) -> str:
    """Expand a bare alert uuid/ref to a full ``/api/3/alerts/<uuid>`` IRI."""
    if ref.startswith("/api/"):
        return ref
    return f"/api/3/alerts/{ref.rstrip('/').split('/')[-1].split(':')[-1]}"


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
        resp = self.client.get(f"/api/3/workflows?{qs}")
        members = (resp or {}).get("hydra:member") or []
        return members[0].get("uuid") if members else None

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
    def runs(
        self,
        *,
        playbook: str | None = None,
        playbook_uuid: str | None = None,
        limit: int = 20,
        raw: bool = False,
        typed: bool = False,
    ) -> list[dict[str, Any]]:
        """List recent playbook runs, newest first (live + historical merged).

        Scope to one playbook by ``playbook`` (name, resolved to uuid) or
        ``playbook_uuid``. Returns shaped dicts
        (``{task_id, name, status, error_message, modified, uuid, pk, source}``)
        by default; pass ``raw=True`` for the full unshaped run records, or
        ``typed=True`` for ``WorkflowRun`` objects (parsed
        from the full records, still dict-compatible). ``typed`` wins over ``raw``.
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

    def get(
        self,
        run_pk: str,
        *,
        raw: bool = False,
        typed: bool = False,
        step_detail: bool = False,
    ) -> dict[str, Any]:
        """Fetch one run by its pk (the trailing id of a run's ``@id``).

        Tries the live table first, then historical. Returns a shaped dict by
        default; ``raw=True`` for the full record, or ``typed=True`` for a
        ``WorkflowRun``. ``typed`` wins over ``raw``.

        Pass ``step_detail=True`` to ask FortiSOAR for the per-step execution
        trace (``?step_detail=true``); the step results land under the run
        record's ``workflow``/``result`` structure. ``step_detail`` implies
        ``raw`` (the shaped view drops the trace), unless ``typed`` is set.
        """
        if not isinstance(run_pk, str) or not run_pk.strip():
            raise ValueError("get() requires a non-empty run pk")
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
        raise ValueError(f"run {run_pk!r} not found")

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
        full = self.get(run_pk, step_detail=True)
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

        Returns:
            The trigger response, typically ``{"task_id": "<run-uuid>"}``.
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
        return self.client.post(f"/api/triggers/1/notrigger/{uuid}", data=body)

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
