"""Pending manual workflow inputs -- ``/api/wf/api/manual-wf-input/``.

The playbooks paused on a Manual Input / Approval step, waiting on a human.
Accessed as ``client.manual_input``.

This is a ``wf`` API entity, not a ``/api/3`` module: the listing is fetched
with ``POST`` to ``list_wfinput/`` (the assignment scope travels in the request
*body* -- ``{"assigned_to": "me"|"myTeams"|"all", "is_approval": bool}``), returns
a hydra envelope, and each row is a :class:`~pyfsr.models.ManualInput`. Rows are
removed with ``DELETE .../manual-wf-input/<id>/`` (204).

Example:
    >>> pending = client.manual_input.list(assigned_to="me")
    >>> pending[0].title
    'Final Go Call'
    >>> client.manual_input.delete(pending[0].id)
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
