"""Pending manual workflow inputs -- ``/api/wf/api/manual-wf-input/``.

The playbooks paused on a Manual Input / Approval step, waiting on a human.
Accessed as ``client.manual_input``.

This is a ``wf`` API entity, not a ``/api/3`` module: the listing is fetched
with ``POST`` to ``list_wfinput/`` (the assignment scope travels in the request
*body* -- ``{"assigned_to": "me"|"myTeams"|"all", "is_approval": bool}``), returns
a hydra envelope, and each row is a :class:`~pyfsr.models.ManualInput`. Rows are
removed with ``DELETE .../manual-wf-input/<id>/`` (204).

To drive a paused prompt in one call (find + fill + resume), use
:meth:`~ManualInputAPI.answer`; :meth:`~ManualInputAPI.list` /
:meth:`~ManualInputAPI.retrieve` / :meth:`~ManualInputAPI.resume` are the
low-level pieces it composes.

Example:
    >>> client = demo_client()
    >>> pending = client.manual_input.list(assigned_to="all")
    >>> pending[0].title  # the prompt's schema title, not the step name
    'Enter a six digit number'
    >>> pending[0].is_approval
    False
    >>> pending[0].response_mapping is None  # list rows omit it; retrieve() has it
    True
"""

from __future__ import annotations

import builtins
import datetime as _dt
import time
from pathlib import Path
from typing import Any, Literal, overload

import requests

from ..models import BaseRecord
from ..models._system import ManualInput, ManualInputOption, ManualInputResume, ManualInputVariable
from ..pagination import paginate_offset
from ..utils.iri import uuid_from_iri
from ._people import resolve_user_iri as _resolve_user_iri
from .base import BaseAPI

_BASE = "/api/wf/api/manual-wf-input/"
_LIST = f"{_BASE}list_wfinput/"

# `wfinput_resume` is NOT a fire-and-forget submit: FortiSOAR runs the resumed
# branch inline and only answers once the run yields again -- at the next gate, a
# delay, or the end of the branch. Answering a gate whose branch runs straight to
# a terminal step therefore holds the connection for the whole branch, which
# routinely outruns the client's 30s default. The answer has LANDED; only the
# response is late. Give it a window sized for a branch, not for a REST call.
_RESUME_TIMEOUT = 180

AssignedTo = Literal["me", "myTeams", "all"]
PromptType = Literal["InputBased", "DecisionBased"]

#: Fields the live ``WorkflowInputView`` actually filters on via ``GET`` query
#: params (``filterset_fields``). ``is_approval`` is deliberately NOT here: the
#: GET view silently ignores it (live-verified 8.0.0) -- approval-only scoping
#: must go through :meth:`ManualInputAPI.list` (the POST ``list_wfinput`` body).
_GET_FILTER_FIELDS = ("id", "record", "type", "workflow", "timeout", "unauthenticated_input")
#: Fields the GET view *declares* in ``ordering_fields``. NB ``name`` is listed
#: but is NOT a column on the WorkflowInput model, so ``?ordering=name`` makes
#: Django raise ``FieldError`` -> HTTP 500 (live-verified 8.0.0; same root cause
#: as the ``search`` 500, whose ``search_fields=('$name','$id')`` hit the same
#: phantom ``name``). Only ``id``/``timeout`` actually work.
_GET_ORDERING_FIELDS = ("id", "name", "timeout")
#: The subset that does not 500 -- what :meth:`find` accepts.
_USABLE_ORDERING_FIELDS = ("id", "timeout")


def _record_iri(record: str | dict[str, Any] | BaseRecord) -> str:
    """Coerce a record reference to its ``@id`` IRI for a ``record=`` filter."""
    if isinstance(record, str):
        return record
    if isinstance(record, BaseRecord):
        iri = getattr(record, "id_iri", None) or getattr(record, "uuid", None)
        if iri:
            return iri
    if isinstance(record, dict):
        iri = record.get("@id") or record.get("iri")
        if iri:
            return iri
    raise ValueError(f"cannot derive a record IRI from {record!r}; pass an '/api/3/<module>/<uuid>' string")


class ManualInputAPI(BaseAPI):
    """List and delete pending manual workflow inputs (Manual Input / Approval steps)."""

    # Note: each row's ``.title`` is the prompt's *schema* title -- the Manual
    # Input step's ``title:``, NOT the step name. The two coincide only when the
    # step declares no ``title:`` (the schema title then defaults to the step
    # name), which is why matching on the step name appears to work until an
    # author sets a real title. Titles are also not unique across runs, so
    # prefer :meth:`pending_for_run` (a run-scoped join) when you hold a
    # ``task_id``; :meth:`answer` (``by_title=``) is the best-effort shortcut.
    @overload
    def list(
        self,
        *,
        assigned_to: AssignedTo = ...,
        is_approval: bool | None = ...,
        unauthenticated_input: bool = ...,
        ordering: str = ...,
        limit: int | None = ...,
        offset: int = ...,
        typed: Literal[True] = ...,
    ) -> builtins.list[ManualInput]: ...
    @overload
    def list(
        self,
        *,
        assigned_to: AssignedTo = ...,
        is_approval: bool | None = ...,
        unauthenticated_input: bool = ...,
        ordering: str = ...,
        limit: int | None = ...,
        offset: int = ...,
        typed: Literal[False],
    ) -> builtins.list[dict[str, Any]]: ...
    @overload
    def list(
        self,
        *,
        assigned_to: AssignedTo = ...,
        is_approval: bool | None = ...,
        unauthenticated_input: bool = ...,
        ordering: str = ...,
        limit: int | None = ...,
        offset: int = ...,
        typed: bool = ...,
    ) -> builtins.list[ManualInput] | builtins.list[dict[str, Any]]: ...
    def list(
        self,
        *,
        assigned_to: AssignedTo = "all",
        is_approval: bool | None = None,
        unauthenticated_input: bool = False,
        ordering: str = "-id",
        limit: int | None = None,
        offset: int = 0,
        typed: bool = True,
    ) -> builtins.list[ManualInput] | builtins.list[dict[str, Any]]:
        """Return pending manual inputs in the given assignment scope.

        This is the *inbox* query (``POST list_wfinput/``): the only place
        ``assigned_to`` and ``is_approval`` filter server-side. To filter by the
        linked ``record``, prompt ``type`` or ``workflow`` run id instead, use
        :meth:`find` (a ``GET`` with real DRF filtering) -- those are ignored here.

        Args:
            assigned_to: assignment scope, sent in the POST body -- ``"all"``
                (default), ``"me"`` (inputs assigned to the caller), or
                ``"myTeams"`` (caller's teams). The default is ``"all"`` on
                purpose: an **unassigned** prompt (a Manual Input step with no
                ``assign_to:``) is invisible to ``"me"``/``"myTeams"``, so the
                old ``"me"`` default silently returned nothing for the common
                unassigned case (live-verified 8.0.0).
            is_approval: ``True`` for approval gates only, ``False`` for plain
                data-input prompts only, ``None`` (default) for both. Sent in the
                POST body.
            unauthenticated_input: include inputs that accept unauthenticated
                submission (default ``False``).
            ordering: sort field (default ``"-id"``, newest first).
            limit: page size. ``None`` (default) fetches every page (following
                ``hydra:nextPage``); pass an int for a single page.
            offset: starting offset for a single-page fetch.
            typed: parse rows into :class:`~pyfsr.models.ManualInput` (default);
                pass ``False`` for raw dicts.

        Example:
            >>> client = demo_client()
            >>> pending = client.manual_input.list(assigned_to="all")
            >>> len(pending)
            1
            >>> pending[0].title  # schema title (step "AskNumber" sets title:)
            'Enter a six digit number'
        """
        params: dict[str, Any] = {
            "format": "json",
            "ordering": ordering,
            "unauthenticated_input": str(unauthenticated_input).lower(),
        }
        body: dict[str, Any] = {"assigned_to": assigned_to}
        if is_approval is not None:
            body["is_approval"] = is_approval

        if limit is not None:
            params["limit"] = limit
            params["offset"] = offset
            members = self._members(self.client.post(_LIST, data=body, params=params))
        else:
            members = paginate_offset(
                lambda page_offset: self.client.post(_LIST, data=body, params=dict(params, offset=page_offset)),
                offset=offset,
            )

        if typed:
            return [ManualInput.model_validate(m) for m in members]
        return members

    def find(
        self,
        *,
        record: str | dict[str, Any] | BaseRecord | None = None,
        type: PromptType | None = None,
        workflow: int | str | None = None,
        is_approval: bool | None = None,
        ordering: str = "-id",
        limit: int | None = None,
        offset: int = 0,
        typed: bool = True,
    ) -> builtins.list[ManualInput] | builtins.list[dict[str, Any]]:
        """Filter pending manual inputs server-side with a ``GET`` (full rows).

        The ``GET /api/wf/api/manual-wf-input/`` view is a real DRF filterset --
        unlike :meth:`list` (the POST inbox query) it filters on the prompt's
        own columns and returns **full** rows (``input`` + ``response_mapping`` +
        the numeric ``workflow`` run id), so a row from here is ready for
        :meth:`resume`/:meth:`answer` without a second :meth:`retrieve`.

        Args:
            record: only inputs linked to this record -- a module record's IRI,
                or a record dict/model (its ``@id`` is used). A Manual Input step
                links a record via its ``record:`` arg; unlinked prompts have an
                empty ``record`` and won't match.
            type: ``"InputBased"`` (prompts with a form) or ``"DecisionBased"``
                (button-only). NB a button-only **approval** gate is stored as
                ``type="DecisionBased", is_approval=True`` -- *not* a distinct
                ``"ApprovalManualInput"`` type (live-verified 8.0.0).
            workflow: a single run's **numeric** pk -- the run-scoped filter the
                UI uses (see :meth:`pending_for_run`, which resolves a ``task_id``
                to this for you).
            is_approval: approval gates only (``True``) / data prompts only
                (``False``). Applied **client-side**: the GET view silently
                ignores an ``is_approval`` query param, so it cannot filter there.
            ordering: sort field -- only ``id`` or ``timeout`` (optional ``-``
                prefix). ``name`` is declared by the view but 500s (it is not a
                real column), so it is rejected here; ``search`` is not offered
                for the same reason (the SearchFilter's ``$name`` 500s on 8.0.0).
            limit: page size (``None`` fetches every page).
            offset: starting offset for a single-page fetch.
            typed: parse into :class:`~pyfsr.models.ManualInput` (default).

        Example:
            >>> client = demo_client()  # doctest: +SKIP
            >>> # every approval gate on the box, newest first:
            >>> client.manual_input.find(type="DecisionBased", is_approval=True)  # doctest: +SKIP
        """
        if type is not None and type not in ("InputBased", "DecisionBased"):
            raise ValueError(f"type must be 'InputBased' or 'DecisionBased', not {type!r}")
        if ordering and ordering.lstrip("-") not in _USABLE_ORDERING_FIELDS:
            hint = (
                " ('name' is declared by the view but 500s -- it is not a real column)"
                if ordering.lstrip("-") == "name"
                else ""
            )
            raise ValueError(
                f"ordering={ordering!r} is not usable; choose from "
                f"{_USABLE_ORDERING_FIELDS} (optionally '-' prefixed){hint}"
            )

        params: dict[str, Any] = {"format": "json", "ordering": ordering}
        if record is not None:
            params["record"] = _record_iri(record)
        if type is not None:
            params["type"] = type
        if workflow is not None:
            params["workflow"] = workflow

        if limit is not None:
            params["limit"] = limit
            params["offset"] = offset
            members = self._members(self.client.get(_BASE, params=params))
        else:
            members = paginate_offset(
                lambda page_offset: self.client.get(_BASE, params=dict(params, offset=page_offset)),
                offset=offset,
            )

        if is_approval is not None:
            members = [m for m in members if bool(m.get("is_approval")) is is_approval]

        if typed:
            return [ManualInput.model_validate(m) for m in members]
        return members

    def pending_for_record(
        self,
        record: str | dict[str, Any] | BaseRecord,
        *,
        is_approval: bool | None = None,
        ordering: str = "-id",
        limit: int | None = None,
        offset: int = 0,
        typed: bool = True,
    ) -> builtins.list[ManualInput] | builtins.list[dict[str, Any]]:
        """Pending manual inputs linked to one record -- ``find(record=...)``.

        The reliable "what is this alert/incident waiting on a human for?" join:
        a Manual Input step that sets ``record:`` surfaces here. ``record`` is the
        record's IRI or a record dict/model.
        """
        return self.find(
            record=record,
            is_approval=is_approval,
            ordering=ordering,
            limit=limit,
            offset=offset,
            typed=typed,
        )

    def pending_for_run(
        self,
        task_id: str,
        *,
        wait: float = 0,
        is_approval: bool | None = None,
        ordering: str = "-id",
        limit: int | None = None,
        offset: int = 0,
        typed: bool = True,
    ) -> builtins.list[ManualInput] | builtins.list[dict[str, Any]]:
        """Pending manual inputs for one playbook run, keyed by its ``task_id``.

        Resolves ``task_id`` -> the run's primary key, then lists the
        manual-wf-input queue scoped to that run via the ``workflow`` query
        filter -- the same filter the FortiSOAR UI uses to show a run's pending
        inputs in its execution history. This is the reliable run -> pending-
        input join: :meth:`list` returns every pending input instance-wide and
        each row's ``workflow`` field is an opaque Fernet token, so it cannot be
        filtered by task_id client-side.

        Unlike :meth:`list` (whose ``list_wfinput/`` rows are summary-only),
        these rows carry the full prompt the UI renders -- ``input.schema``
        (title/description/``inputVariables``), ``response_mapping``'s buttons,
        and the **numeric** run id in ``workflow`` -- so a row from here has
        everything :meth:`resume` needs without a second :meth:`retrieve` call.
        Live-verified on 8.0.0.

        Each row's ``title`` is the prompt's schema title (the Manual Input
        step's ``title:``), not the step name.

        An approval gate -- a Manual Input step with ``is_approval: true`` --
        surfaces here with ``is_approval=True`` even though it does NOT appear in
        the ``approvals`` module. Its ``type`` is the ordinary ``InputBased`` or
        ``DecisionBased`` (a button-only gate is ``DecisionBased``); there is no
        separate ``"ApprovalManualInput"`` type on the wire -- ``is_approval`` is
        the only distinguishing flag (live-verified 8.0.0). (This is distinct
        from the legacy ``approval`` step type, which writes to the ``approvals``
        module and never reaches this queue.) This is how you find the
        ``manual_input_id`` to drive the gate: the modern approval step resumes
        via ``wfinput_resume`` (see :meth:`approve` / :meth:`reject`), not the
        legacy ``/approval/`` endpoint.

        Args:
            task_id: the ``task_id`` returned by
                :meth:`~pyfsr.api.playbooks.PlaybooksAPI.trigger`.
            wait: seconds to keep re-checking ``log_list`` for the run to
                **register** before giving up. A run appears in the log store a
                beat after ``trigger()`` returns (measured: up to ~10s on
                8.0.0), so a caller that resumes straight after triggering
                should pass ``wait=15`` -- with the default ``0`` an immediate
                empty result may simply mean "not registered yet", not "no
                pending inputs".
            is_approval: client-side filter -- ``True`` for approval gates only,
                ``False`` for plain data-input prompts only, ``None`` (default)
                for both.
            ordering: sort field (default ``"-id"``, newest first).
            limit: page size. ``None`` (default) fetches every page (following
                ``hydra:nextPage``); pass an int for a single page.
            offset: starting offset for a single-page fetch.
            typed: parse rows into :class:`~pyfsr.models.ManualInput` (default);
                pass ``False`` for raw dicts.

        Returns:
            The run's pending inputs. Empty when the run has none (it already
            finished, is not paused on a manual input/approval step) -- or,
            right after a trigger, when it has not **registered** yet; pass
            ``wait=`` a few seconds to absorb that race.

        Example:
            >>> client = demo_client()  # doctest: +SKIP
            >>> run = client.playbooks.trigger("Triage Alert", follow=False)  # doctest: +SKIP
            >>> pending = client.manual_input.pending_for_run(run.task_id, wait=15)  # doctest: +SKIP
            >>> pending[0].input["schema"]["title"]  # doctest: +SKIP
            'Approve ingestion of the fetched indicators?'

            .. note::
                Requires playbook/run state setup, so this example is ``+SKIP``.
        """
        # task_id -> run pk (the @id path's trailing segment). The run shows up
        # in the log store a beat after trigger() answers, so re-check until
        # `wait` elapses (the default 0 keeps the old single-shot behaviour).
        deadline = time.monotonic() + max(0.0, wait)
        rpk = None
        while True:
            resp = self.client.playbooks.log_list(task_id=task_id, limit=1)
            members = (
                resp.get("hydra:member", []) if isinstance(resp, dict) else (resp if isinstance(resp, list) else [])
            )
            if members and isinstance(members[0], dict):
                rpk = uuid_from_iri(members[0].get("@id"))
                if rpk:
                    break
            if time.monotonic() >= deadline:
                return []
            time.sleep(2.0)

        params: dict[str, Any] = {
            "format": "json",
            "workflow": rpk,
            "ordering": ordering,
        }
        if limit is not None:
            params["limit"] = limit
            params["offset"] = offset
            page = self._members(self.client.get(_BASE, params=params))
        else:
            page = paginate_offset(
                lambda page_offset: self.client.get(_BASE, params=dict(params, offset=page_offset)),
                offset=offset,
            )

        if is_approval is not None:
            page = [m for m in page if bool(m.get("is_approval")) is is_approval]

        if typed:
            return [ManualInput.model_validate(m) for m in page]
        return page

    def count(
        self,
        *,
        assigned_to: AssignedTo = "me",
        is_approval: bool | None = None,
        unauthenticated_input: bool = False,
    ) -> int:
        """Total pending-input count (``hydra:totalItems``) for the given scope."""
        params: dict[str, Any] = {
            "format": "json",
            "limit": 1,
            "offset": 0,
            "ordering": "-id",
            "unauthenticated_input": str(unauthenticated_input).lower(),
        }
        body: dict[str, Any] = {"assigned_to": assigned_to}
        if is_approval is not None:
            body["is_approval"] = is_approval
        resp = self.client.post(_LIST, data=body, params=params)
        if isinstance(resp, dict):
            return int(resp.get("hydra:totalItems") or 0)
        return 0

    @overload
    def retrieve(
        self,
        input_id: int | str,
        *,
        owners: str | builtins.list[str] | None = ...,
        unauthenticated_input: bool = ...,
        typed: Literal[True] = ...,
    ) -> ManualInput: ...
    @overload
    def retrieve(
        self,
        input_id: int | str,
        *,
        owners: str | builtins.list[str] | None = ...,
        unauthenticated_input: bool = ...,
        typed: Literal[False],
    ) -> dict[str, Any]: ...
    @overload
    def retrieve(
        self,
        input_id: int | str,
        *,
        owners: str | builtins.list[str] | None = ...,
        unauthenticated_input: bool = ...,
        typed: bool = ...,
    ) -> ManualInput | dict[str, Any]: ...
    def retrieve(
        self,
        input_id: int | str,
        *,
        owners: str | builtins.list[str] | None = None,
        unauthenticated_input: bool = False,
        typed: bool = True,
    ) -> ManualInput | dict[str, Any]:
        """Open one manual input (``POST .../<id>/retrieve_wfinput/``).

        Returns the full prompt the UI renders when a user opens the item: the
        list shape plus ``input`` (the input ``schema`` -- title/description/
        ``inputVariables``), ``response_mapping`` (approval/button options and
        messages), and ``custom_fields`` (custom email overrides). Here
        ``workflow`` is the numeric run id rather than the list's encrypted token.

        Args:
            input_id: the manual input's ``id`` (from :meth:`list`).
            owners: the owner IRI(s) to claim/scope the retrieve with -- a single
                ``/api/3/people/<uuid>`` or ``/api/3/teams/<uuid>`` IRI, or a list
                (sent as repeated ``owners`` query params, as the UI does).
            unauthenticated_input: pass ``True`` for an unauthenticated-input item.
            typed: return a :class:`~pyfsr.models.ManualInput` (default); pass
                ``False`` for the raw dict.

        Example:
            >>> client = demo_client()
            >>> mi = client.manual_input.retrieve(1)
            >>> mi.title  # the row's title IS the schema title, mirrored
            'Enter a six digit number'
            >>> mi.input["schema"]["title"]
            'Enter a six digit number'
            >>> mi.workflow  # numeric run id here, encrypted token on list()
            1
        """
        params: dict[str, Any] = {
            "format": "json",
            "unauthenticated_input": str(unauthenticated_input).lower(),
        }
        if owners is not None:
            params["owners"] = owners  # requests serializes a list as repeated keys
        resp = self.client.post(f"{_BASE}{input_id}/retrieve_wfinput/", params=params)
        if typed:
            return ManualInput.model_validate(resp)
        return resp

    def resume(
        self,
        workflow_id: int | str,
        *,
        step_iri: str,
        step_id: int,
        manual_input_id: int,
        user: str,
        input: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> ManualInputResume:
        """Submit a manual input and resume its paused playbook.

        ``POST /api/wf/api/workflows/<workflow_id>/wfinput_resume/`` with the
        chosen button (``step_iri``) and any collected input values. Returns a
        :class:`~pyfsr.models.ManualInputResume` (``task_id`` + ``message``).

        .. note::
           The submit is synchronous in the part that matters: FortiSOAR executes
           the resumed branch inline and answers only when the run next yields --
           at another gate, a delay, or the end of the branch. A branch that runs
           straight to a terminal step is executed in full inside this call, so it
           can take far longer than an ordinary REST call. This method therefore
           uses its own generous timeout (180s) instead of the client default,
           and treats a read timeout as "submitted, still running" rather than as
           a failure -- it re-checks whether the input was consumed and only
           raises if it is genuinely still pending. The request is never retried:
           a retry would risk resuming the run twice.

        Args:
            workflow_id: the numeric run id -- the ``workflow`` field from
                :meth:`retrieve` (not the list's encrypted token).
            step_iri: the selected response option's ``step_iri`` (from
                ``retrieve(...).response_mapping["options"]``); picks which branch
                the playbook resumes down.
            step_id: the paused step's ``step_id`` (from :meth:`retrieve`/:meth:`list`).
            manual_input_id: the manual input ``id``.
            user: the submitting user's IRI (``/api/3/people/<uuid>``).
            input: collected input values keyed by variable name, e.g.
                ``{"test": "def"}`` -- omit for an approval/button-only step.
            timeout: read timeout in seconds for this submit. Defaults to
                ``max(client.timeout, 180)`` -- see the note above. Pass a larger
                value when a branch is known to be slow (a package install, say).

        Example:
            >>> client = demo_client()
            >>> result = client.manual_input.resume(
            ...     workflow_id=1,
            ...     step_iri="/api/wf/api/workflows/1/steps/100",
            ...     step_id=100,
            ...     manual_input_id=1,
            ...     user="/api/3/people/00000000-0000-0000-0000-000000000001",
            ...     input={"test_var": "test_value"}
            ... )
            >>> result["message"]
            'Awaiting Playbook resumed successfully.'

        See also:
            :meth:`answer` -- finds the pending input, resolves the numeric run
            id / ``step_iri`` / user IRI, and resumes in a single call.
        """
        body: dict[str, Any] = {
            "input": input or {},
            "step_iri": step_iri,
            "step_id": step_id,
            "manual_input_id": manual_input_id,
            "user": user,
        }
        if timeout is None:
            # getattr: BaseAPI is handed anything client-shaped (test doubles included).
            timeout = max(float(getattr(self.client, "timeout", 0) or 0), float(_RESUME_TIMEOUT))
        try:
            resp = self.client.post(
                f"/api/wf/api/workflows/{workflow_id}/wfinput_resume/",
                data=body,
                timeout=timeout,
            )
        except requests.exceptions.ReadTimeout:
            # The submit reached the server -- what timed out is waiting for the
            # branch it kicked off. Deliberately NOT retried: a second POST could
            # resume the run twice. Instead ask the server whether the input was
            # consumed, which is the fact the caller actually needs.
            if self._still_pending(manual_input_id):
                raise
            return ManualInputResume(
                task_id=None,
                message=(
                    f"Manual input {manual_input_id} was accepted and the playbook "
                    f"resumed, but the resumed branch was still running when the "
                    f"read timed out after {timeout:g}s. The input is no longer "
                    f"pending. Poll the run for its outcome, or pass a larger "
                    f"timeout= if this branch is expected to be slow."
                ),
            )
        return ManualInputResume.model_validate(resp)

    def _still_pending(self, manual_input_id: int) -> bool:
        """Is this manual input still waiting on a human?

        Used to tell "the submit never landed" from "the submit landed and the
        branch outran the read timeout". A lookup failure is reported as still
        pending, so an ambiguous case surfaces the original timeout rather than a
        false all-clear.
        """
        try:
            rows = self.list(assigned_to="all", typed=False)
        except Exception:
            return True
        return any(int(r.get("id", -1)) == int(manual_input_id) for r in rows)

    def answer(
        self,
        value: Any = None,
        *,
        by_title: str | None = None,
        input_id: int | str | None = None,
        inputs: dict[str, Any] | None = None,
        user: str | None = None,
        option: int | str = 0,
        assigned_to: AssignedTo = "all",
        timeout: float | None = None,
    ) -> ManualInputResume:
        """Find a pending manual input, fill it, and resume its playbook -- one call.

        This is the high-level twin of :meth:`list` + :meth:`retrieve` +
        :meth:`resume`. It hides the foot-guns those three expose when wired by
        hand:

        * a pending input's ``.title`` is the prompt's **schema title** (the
          Manual Input step's ``title:``), not the step name -- ``by_title=``
          matches that, and only falls back to the step name for a step that
          declares no ``title:``;
        * :meth:`list` returns an encrypted ``workflow`` token while
          :meth:`resume` needs the **numeric** run id -- this pulls the numeric
          id from :meth:`retrieve` for you;
        * the submit button is a ``response_mapping`` option with a ``step_iri``
          -- this resolves ``option`` (index or label) to that ``step_iri``;
        * ``resume`` needs a submitting-user IRI -- if ``user`` is omitted this
          resolves one from ``/api/3/people`` (preferring an admin).

        Args:
            value: a scalar answer for a **single**-variable input prompt; mapped
                to the input's declared variable name automatically. On a prompt
                with no input variables (an approval, or a message with buttons)
                it names the button to press instead -- a label or an index, the
                same as ``option=``. For a multi-variable prompt, use ``inputs=``.
            by_title: select the pending input whose ``title`` -- the prompt's
                schema title, i.e. the Manual Input step's ``title:`` -- matches
                exactly. Titles are not unique (the same step pausing in two
                runs yields two identically-titled rows), so an ambiguous match
                raises rather than guessing; pass ``input_id=`` or use
                :meth:`pending_for_run` to disambiguate. Mutually exclusive with
                ``input_id``.
            input_id: select the pending input by its numeric ``id`` directly.
            inputs: collected values keyed by variable name, e.g.
                ``{"my_number": 654321}`` -- use instead of ``value`` when the
                prompt declares more than one variable.
            user: submitting user's IRI (``/api/3/people/<uuid>``); auto-resolved
                when omitted.
            option: which response option to submit -- an index (default ``0``,
                the primary button) or the option's ``label``.
            assigned_to: assignment scope to search for the pending input
                (default ``"all"``).

        Returns:
            the :class:`~pyfsr.models.ManualInputResume` ack (``task_id`` +
            ``message``); the resume is asynchronous.

        Example:
            >>> client = demo_client()  # doctest: +SKIP
            >>> # by_title is the step's `title:`, not its name ("AskNumber"):
            >>> client.manual_input.answer(654321, by_title="Enter a six digit number")  # doctest: +SKIP
            {'task_id': '...', 'message': 'Awaiting Playbook resumed successfully.'}

            .. note::
                Requires playbook state setup, so this example is ``+SKIP``.

        See also:
            :meth:`resume` for the low-level form when you already hold the
            ``step_iri`` / numeric run id -- and for why ``timeout=`` exists and
            what a read timeout here does and does not mean.
        """
        if (by_title is None) == (input_id is None):
            raise ValueError("pass exactly one of by_title= or input_id=")

        if input_id is None:
            assert by_title is not None
            input_id = self._find_by_title(by_title, assigned_to)

        user_iri = user or self._resolve_user_iri()

        assert input_id is not None  # resolved above from by_title or passed directly
        full = self.retrieve(input_id, owners=user_iri)
        assert isinstance(full, ManualInput)
        options = (full.response_mapping.options if full.response_mapping else None) or []
        if not options:
            raise LookupError(f"manual input {input_id} exposes no response options")
        if inputs is None and value is not None and not self._variable_names(full):
            # A prompt with only buttons has no field to put a value in: the
            # value names the button (label or index), as for an approval.
            option, value = value, None
        opt = self._pick_option(options, option)

        # A button is wired to its next step at author time: the step's
        # `next:` becomes the option's step_iri. A Manual Input step with no
        # `next:` compiles to an option without one, and wfinput_resume 500s on
        # a null (or absent) step_iri -- fail here with the cause instead.
        step_iri = opt.get("step_iri")
        if not step_iri:
            raise ValueError(
                f"response option {opt.get('option')!r} on manual input {input_id} has no "
                f"step_iri, so the run cannot be resumed: the Manual Input step has no "
                f"next step wired to this button (no `next:` on the step). Fix the "
                f"playbook -- the paused run is unresumable via the API as authored."
            )

        if inputs is None and value is not None:
            inputs = self._map_scalar(full, value)
        if inputs:
            inputs = self._coerce_inputs(full, inputs)

        assert full.workflow is not None  # retrieve always populates the numeric run id
        assert full.step_id is not None
        return self.resume(
            full.workflow,
            step_iri=step_iri,
            step_id=full.step_id,
            manual_input_id=int(input_id),
            user=user_iri,
            input=inputs,
            timeout=timeout,
        )

    def approve(
        self,
        *,
        input_id: int | str | None = None,
        by_title: str | None = None,
        comment: str | None = None,
        inputs: dict[str, Any] | None = None,
        user: str | None = None,
        assigned_to: AssignedTo = "all",
        timeout: float | None = None,
    ) -> ManualInputResume:
        """Approve a paused **approval gate** and resume its playbook.

        Presses the gate's *primary* (Approve) button. Select the gate with
        ``input_id=`` or ``by_title=`` (exactly one). A ``comment`` is recorded
        only if the approval step collects a text field for it (named like
        ``comment``/``message``/``reason``/``note``); otherwise pass the field
        values through ``inputs=``. See :meth:`reject` for the mirror.
        """
        return self._decide(
            approve=True,
            input_id=input_id,
            by_title=by_title,
            comment=comment,
            inputs=inputs,
            user=user,
            assigned_to=assigned_to,
            timeout=timeout,
        )

    def reject(
        self,
        *,
        input_id: int | str | None = None,
        by_title: str | None = None,
        comment: str | None = None,
        inputs: dict[str, Any] | None = None,
        user: str | None = None,
        assigned_to: AssignedTo = "all",
        timeout: float | None = None,
    ) -> ManualInputResume:
        """Reject a paused approval gate and resume down its reject branch.

        Presses the gate's non-primary (Reject) button. Raises if the prompt has
        only one option (then it is not an approve/reject gate -- use
        :meth:`answer`). Arguments mirror :meth:`approve`.
        """
        return self._decide(
            approve=False,
            input_id=input_id,
            by_title=by_title,
            comment=comment,
            inputs=inputs,
            user=user,
            assigned_to=assigned_to,
            timeout=timeout,
        )

    def _decide(
        self,
        *,
        approve: bool,
        input_id: int | str | None,
        by_title: str | None,
        comment: str | None,
        inputs: dict[str, Any] | None,
        user: str | None,
        assigned_to: AssignedTo,
        timeout: float | None,
    ) -> ManualInputResume:
        if (by_title is None) == (input_id is None):
            raise ValueError("pass exactly one of by_title= or input_id=")
        if input_id is None:
            assert by_title is not None
            input_id = self._find_by_title(by_title, assigned_to)

        user_iri = user or self._resolve_user_iri()
        full = self.retrieve(input_id, owners=user_iri)
        assert isinstance(full, ManualInput)
        options = (full.response_mapping.options if full.response_mapping else None) or []
        if not options:
            raise LookupError(f"manual input {input_id} exposes no response options")

        opt = self._decision_option(options, approve=approve)
        step_iri = opt.get("step_iri") if isinstance(opt, dict) else opt.step_iri
        if not step_iri:
            label = opt.get("option") if isinstance(opt, dict) else opt.option
            raise ValueError(
                f"the {'approve' if approve else 'reject'} option {label!r} on manual input "
                f"{input_id} has no step_iri (the Manual Input step has no `next:` wired to it), "
                f"so the run cannot be resumed down that branch."
            )

        merged = self._decision_inputs(full, comment, inputs)
        if merged:
            merged = self._coerce_inputs(full, merged)

        assert full.workflow is not None
        assert full.step_id is not None
        return self.resume(
            full.workflow,
            step_iri=step_iri,
            step_id=full.step_id,
            manual_input_id=int(input_id),
            user=user_iri,
            input=merged,
            timeout=timeout,
        )

    def _find_by_title(self, by_title: str, assigned_to: AssignedTo) -> int:
        """Resolve a pending input by its (schema) title to a numeric id, or raise."""
        rows = self.list(assigned_to=assigned_to)
        matches = [mi for mi in rows if (mi.title or "") == by_title]
        if not matches:
            available = sorted({mi.title for mi in rows if mi.title})
            raise LookupError(
                f"no pending manual input titled {by_title!r} in scope "
                f"assigned_to={assigned_to!r}. .title is the prompt's schema "
                f"title (the Manual Input step's `title:`), not the step name "
                f"-- it only matches the step name when the step sets no "
                f"`title:`. Pending titles in scope: {available or 'none'}"
            )
        if len(matches) > 1:
            raise LookupError(
                f"{len(matches)} pending manual inputs are titled {by_title!r} "
                f"(ids {[mi.id for mi in matches]}) -- titles are not unique, so "
                f"acting by title could resume the wrong run. Pass input_id=, "
                f"or use pending_for_run(task_id) to scope to one run."
            )
        assert matches[0].id is not None
        return int(matches[0].id)

    @staticmethod
    def _decision_option(
        options: builtins.list[dict[str, Any]] | builtins.list[ManualInputOption],
        *,
        approve: bool,
    ) -> dict[str, Any] | ManualInputOption:
        def is_primary(o: Any) -> bool:
            return bool(o.get("primary") if isinstance(o, dict) else getattr(o, "primary", None))

        if approve:
            for o in options:
                if is_primary(o):
                    return o
            return options[0]  # no explicit primary -> first button is Approve
        # reject: the non-primary / other button
        non_primary = [o for o in options if not is_primary(o)]
        if len(options) == 1:
            raise LookupError(
                "this prompt has a single response option, so it is not an approve/reject "
                "gate -- use answer() to press its one button."
            )
        return non_primary[0] if non_primary else options[-1]

    def _decision_inputs(
        self,
        full: ManualInput,
        comment: str | None,
        inputs: dict[str, Any] | None,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = dict(inputs or {})
        if comment is None:
            return merged
        varmap = self._var_map(full)
        for name in varmap:
            if name in merged:
                continue
            if any(tok in name.lower() for tok in ("comment", "message", "reason", "note", "remark")):
                merged[name] = comment
                return merged
        raise ValueError(
            "comment= was given but this approval step collects no text field for it "
            f"(prompt fields: {sorted(varmap) or 'none'}). An in-system approval has no "
            "comment channel unless the step declares one -- drop comment=, or pass the "
            "field explicitly via inputs=."
        )

    def _resolve_user_iri(self) -> str:
        """A people IRI to submit a manual input as (prefer an admin)."""
        return _resolve_user_iri(self.client)

    @staticmethod
    def _pick_option(
        options: builtins.list[dict[str, Any]] | builtins.list[ManualInputOption],
        option: int | str,
    ) -> dict[str, Any] | ManualInputOption:
        if isinstance(option, int):
            if not -len(options) <= option < len(options):
                raise IndexError(f"option index {option} out of range (input has {len(options)} option(s))")
            return options[option]
        for opt in options:
            if opt.get("option") == option:
                return opt
        labels = [opt.get("option") for opt in options]
        raise LookupError(f"no response option labelled {option!r}; available: {labels}")

    @staticmethod
    def _variable_names(full: ManualInput) -> builtins.list[str]:
        schema_ = full.input.schema_ if full.input else None
        variables = (schema_.inputVariables if schema_ else None) or []
        return [v.name for v in variables if v.name]

    @classmethod
    def _map_scalar(cls, full: ManualInput, value: Any) -> dict[str, Any]:
        names = cls._variable_names(full)
        if len(names) != 1:
            raise ValueError(
                f"prompt declares {len(names)} input variable(s) ({names}); pass a full "
                f"inputs={{name: value}} dict instead of a scalar value="
            )
        return {names[0]: value}

    @staticmethod
    def _var_map(full: ManualInput) -> dict[str, ManualInputVariable]:
        schema_ = full.input.schema_ if full.input else None
        variables = (schema_.inputVariables if schema_ else None) or []
        return {v.name: v for v in variables if v.name}

    @staticmethod
    def _var_kind(var: ManualInputVariable | None) -> str:
        """A normalized, lower-cased widget kind for coercion decisions."""
        if var is None:
            return ""
        return (var.formType or var.dataType or var.type or "").lower()

    def _coerce_inputs(self, full: ManualInput, inputs: dict[str, Any]) -> dict[str, Any]:
        """Turn Python-friendly answer values into the wire shapes the box expects.

        Best-effort and never fatal: a value already in wire form (an IRI string,
        a JSON-native scalar/dict) is left untouched, and any coercion that cannot
        be made confidently passes the value through for the server to validate.
        Handles record objects/dicts -> ``@id`` (lookups), ``datetime``/``date``
        -> ISO 8601, and a local file path on a file/image field -> an uploaded
        attachment IRI. Lists are coerced element-wise.
        """
        varmap = self._var_map(full)
        return {name: self._coerce_value(varmap.get(name), value) for name, value in inputs.items()}

    def _coerce_value(self, var: ManualInputVariable | None, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return [self._coerce_value(var, v) for v in value]
        if isinstance(value, BaseRecord):
            return getattr(value, "id_iri", None) or getattr(value, "uuid", None) or value
        if isinstance(value, dict) and ("@id" in value or "iri" in value):
            return value.get("@id") or value.get("iri")
        if isinstance(value, _dt.datetime) or isinstance(value, _dt.date):
            return value.isoformat()
        if isinstance(value, str) and self._var_kind(var) in ("file", "image"):
            try:
                if Path(value).is_file():
                    att = self.client.attachments.create_from_file(value)
                    return _record_iri(att if isinstance(att, (dict, BaseRecord)) else {"@id": att})
            except Exception:
                pass  # fall through: let the box reject it with a real message
        return value

    def delete(self, input_id: int | str) -> None:
        """Delete a pending manual input by its ``id`` (``DELETE .../<id>/``, 204).

        Removes the waiting input -- the paused playbook step is abandoned. There
        is no undo.

        Args:
            input_id: the manual input's integer ``id`` (from :meth:`list`).

        Example:
            >>> client = demo_client()
            >>> client.manual_input.delete(2)
        """
        self.client.delete(f"{_BASE}{input_id}/", params={"format": "json"})

    @staticmethod
    def _members(resp: Any) -> builtins.list[dict[str, Any]]:
        if isinstance(resp, dict):
            return resp.get("hydra:member") or resp.get("results") or []
        return resp or []
