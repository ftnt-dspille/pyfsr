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
from typing import Any, Literal, overload

from ..models._system import ManualInput, ManualInputOption, ManualInputResume
from ..pagination import paginate_offset
from ..utils.iri import uuid_from_iri
from .base import BaseAPI

_BASE = "/api/wf/api/manual-wf-input/"
_LIST = f"{_BASE}list_wfinput/"

AssignedTo = Literal["me", "myTeams", "all"]


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
        assigned_to: AssignedTo = "me",
        is_approval: bool | None = None,
        unauthenticated_input: bool = False,
        ordering: str = "-id",
        limit: int | None = None,
        offset: int = 0,
        typed: bool = True,
    ) -> builtins.list[ManualInput] | builtins.list[dict[str, Any]]:
        """Return pending manual inputs in the given assignment scope.

        Args:
            assigned_to: assignment scope, sent in the POST body -- ``"me"``
                (default, inputs assigned to the caller), ``"myTeams"`` (caller's
                teams), or ``"all"``.
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

    def pending_for_run(
        self,
        task_id: str,
        *,
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

        An approval gate -- a Manual Input step with ``is_approval: true``,
        which the wire reports as ``type: "ApprovalManualInput"`` -- surfaces
        here with ``is_approval=True`` even though it does NOT appear in the
        ``approvals`` module. (This is distinct from the legacy ``approval``
        step type, which writes to the ``approvals`` module and never reaches
        this queue.) This is how you find the ``manual_input_id`` to pass to
        :meth:`resume` to drive the gate: the modern approval step resumes via
        ``wfinput_resume``, not the ``/approval/`` endpoint.

        Args:
            task_id: the ``task_id`` returned by
                :meth:`~pyfsr.api.playbooks.PlaybooksAPI.trigger`.
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
            The run's pending inputs (empty if the run has none -- it has
            already finished or is not paused on a manual input/approval step).

        Example:
            >>> client = demo_client()  # doctest: +SKIP
            >>> run = client.playbooks.trigger("Triage Alert", follow=False)  # doctest: +SKIP
            >>> pending = client.manual_input.pending_for_run(run.task_id)  # doctest: +SKIP
            >>> pending[0].input["schema"]["title"]  # doctest: +SKIP
            'Approve ingestion of the fetched indicators?'

            .. note::
                Requires playbook/run state setup, so this example is ``+SKIP``.
        """
        # task_id -> run pk (the @id path's trailing segment).
        resp = self.client.playbooks.log_list(task_id=task_id, limit=1)
        members = resp.get("hydra:member", []) if isinstance(resp, dict) else (resp if isinstance(resp, list) else [])
        if not members or not isinstance(members[0], dict):
            return []
        rpk = uuid_from_iri(members[0].get("@id"))
        if not rpk:
            return []

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
    ) -> ManualInputResume:
        """Submit a manual input and resume its paused playbook.

        ``POST /api/wf/api/workflows/<workflow_id>/wfinput_resume/`` with the
        chosen button (``step_iri``) and any collected input values. Returns a
        :class:`~pyfsr.models.ManualInputResume` (``task_id`` + ``message``); the
        resume is asynchronous.

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
        resp = self.client.post(f"/api/wf/api/workflows/{workflow_id}/wfinput_resume/", data=body)
        return ManualInputResume.model_validate(resp)

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
                to the input's declared variable name automatically. For an
                approval/button-only step, omit it. For a multi-variable prompt,
                use ``inputs=`` instead.
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
            ``step_iri`` / numeric run id.
        """
        if (by_title is None) == (input_id is None):
            raise ValueError("pass exactly one of by_title= or input_id=")

        if input_id is None:
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
                    f"answering by title could resume the wrong run. Pass input_id=, "
                    f"or use pending_for_run(task_id) to scope to one run."
                )
            input_id = matches[0].id

        user_iri = user or self._resolve_user_iri()

        assert input_id is not None  # resolved above from by_title or passed directly
        full = self.retrieve(input_id, owners=user_iri)
        assert isinstance(full, ManualInput)
        options = (full.response_mapping.options if full.response_mapping else None) or []
        if not options:
            raise LookupError(f"manual input {input_id} exposes no response options")
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

        assert full.workflow is not None  # retrieve always populates the numeric run id
        assert full.step_id is not None
        return self.resume(
            full.workflow,
            step_iri=step_iri,
            step_id=full.step_id,
            manual_input_id=int(input_id),
            user=user_iri,
            input=inputs,
        )

    def _resolve_user_iri(self) -> str:
        """A people IRI to submit a manual input as (prefer an admin)."""
        resp = self.client.get("/api/3/people", params={"$limit": 50})
        members = resp.get("hydra:member", []) if isinstance(resp, dict) else []
        if not members:
            raise LookupError("no people records found to submit the manual input as")
        for m in members:
            name = f"{m.get('firstname') or ''} {m.get('lastname') or ''}".lower()
            if "admin" in name:
                return m["@id"]
        return members[0]["@id"]

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
    def _map_scalar(full: ManualInput, value: Any) -> dict[str, Any]:
        schema_ = full.input.schema_ if full.input else None
        variables = (schema_.inputVariables if schema_ else None) or []
        names = [v.name for v in variables if v.name]
        if len(names) != 1:
            raise ValueError(
                f"prompt declares {len(names)} input variable(s) ({names}); pass a full "
                f"inputs={{name: value}} dict instead of a scalar value="
            )
        return {names[0]: value}

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
