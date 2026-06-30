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
    >>> pending = client.manual_input.list(assigned_to="me")
    >>> pending[0].title          # NB: this is the STEP name, not the schema title
    'AskNumber'
    >>> client.manual_input.answer(654321, by_title="AskNumber")
"""

from __future__ import annotations

from typing import Any, Literal

from ..models._system import ManualInput, ManualInputResume
from .base import BaseAPI

_BASE = "/api/wf/api/manual-wf-input/"
_LIST = f"{_BASE}list_wfinput/"

AssignedTo = Literal["me", "myTeams", "all"]


class ManualInputAPI(BaseAPI):
    """List and delete pending manual workflow inputs (Manual Input / Approval steps)."""

    # Note: each row's ``.title`` is the *step name*, not the prompt's schema
    # title. To select and answer a prompt by step name in one call, use
    # :meth:`answer` (``by_title=``) rather than matching ``.title`` by hand.
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
    ) -> list[ManualInput] | list[dict[str, Any]]:
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
            >>> client.manual_input.list(assigned_to="all", is_approval=True)
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
            members = []
            page_offset = offset
            while True:
                page_params = dict(params, offset=page_offset)
                resp = self.client.post(_LIST, data=body, params=page_params)
                page = self._members(resp)
                members.extend(page)
                next_page = resp.get("hydra:nextPage") if isinstance(resp, dict) else None
                per_page = resp.get("hydra:itemsPerPage") if isinstance(resp, dict) else None
                if not next_page or not page:
                    break
                page_offset += per_page or len(page)

        if typed:
            return [ManualInput.model_validate(m) for m in members]
        return members

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

    def retrieve(
        self,
        input_id: int | str,
        *,
        owners: str | list[str] | None = None,
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
            >>> mi = client.manual_input.retrieve(1, owners="/api/3/people/<uuid>")
            >>> mi.input["schema"]["title"]
            'Final Go Call'
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
            >>> mi = client.manual_input.retrieve(3)
            >>> opt = mi.response_mapping["options"][0]      # the "primary" button
            >>> client.manual_input.resume(
            ...     mi.workflow, step_iri=opt["step_iri"], step_id=mi.step_id,
            ...     manual_input_id=mi.id, user="/api/3/people/<uuid>",
            ...     input={"test": "def"})
            {'task_id': '...', 'message': 'Awaiting Playbook resumed successfully.'}

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

        * a pending input's ``.title`` is the **step name**, not the schema
          title -- so you select by step name with ``by_title=``;
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
            by_title: select the pending input whose ``title`` (the step name)
                matches; the newest match is used. Mutually exclusive with
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
            >>> # re-prompt loop: answer the "AskNumber" step until it passes
            >>> client.manual_input.answer(123, by_title="AskNumber")
            {'task_id': '...', 'message': 'Awaiting Playbook resumed successfully.'}

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
                raise LookupError(
                    f"no pending manual input with title (step name) {by_title!r} "
                    f"in scope assigned_to={assigned_to!r}; remember .title is the "
                    f"step name, not the schema title"
                )
            input_id = matches[0].id

        user_iri = user or self._resolve_user_iri()

        full = self.retrieve(input_id, owners=user_iri)
        options = (full.response_mapping or {}).get("options") or []
        if not options:
            raise LookupError(f"manual input {input_id} exposes no response options")
        opt = self._pick_option(options, option)

        if inputs is None and value is not None:
            inputs = self._map_scalar(full, value)

        return self.resume(
            full.workflow,
            step_iri=opt["step_iri"],
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
    def _pick_option(options: list[dict[str, Any]], option: int | str) -> dict[str, Any]:
        if isinstance(option, int):
            if not -len(options) <= option < len(options):
                raise IndexError(f"option index {option} out of range (input has {len(options)} option(s))")
            return options[option]
        for opt in options:
            if opt.get("label") == option:
                return opt
        labels = [opt.get("label") for opt in options]
        raise LookupError(f"no response option labelled {option!r}; available: {labels}")

    @staticmethod
    def _map_scalar(full: ManualInput, value: Any) -> dict[str, Any]:
        variables = ((full.input or {}).get("schema") or {}).get("inputVariables") or []
        names = [v.get("name") for v in variables if v.get("name")]
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
            >>> client.manual_input.delete(2)
        """
        self.client.delete(f"{_BASE}{input_id}/", params={"format": "json"})

    @staticmethod
    def _members(resp: Any) -> list[dict[str, Any]]:
        if isinstance(resp, dict):
            return resp.get("hydra:member") or resp.get("results") or []
        return resp or []
