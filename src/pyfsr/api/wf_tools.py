"""Workflow-engine authoring helpers: Jinja rendering and global variables.

Wraps the two ``/api/wf/api`` endpoints used when authoring/debugging a
playbook outside the visual editor. Accessed as ``client.wf_tools``.

Example:
    >>> client = demo_client()
    >>> client.wf_tools.render("{{ vars.x + 2 }}", {"vars": {"x": 5}})
    'Rendered Jinja output'
    >>> client.wf_tools.dynamic_variable("Default_Indicator_TTL_Days")
    '20'
"""

from __future__ import annotations

from typing import Any

from ..pagination import extract_members
from .base import BaseAPI


class WfToolsAPI(BaseAPI):
    """Jinja rendering + FortiSOAR global ("dynamic") variables."""

    def __init__(self, client):
        super().__init__(client)

    # ------------------------------------------------------------- jinja
    def render(self, template: str, values: dict[str, Any] | None = None) -> Any:
        """Render a Jinja ``template`` server-side and return the result value.

        Uses the workflow engine's own renderer
        (``POST /api/wf/api/jinja-editor/``) so the output matches what a running
        playbook would produce. ``values`` is the context dict (typically
        ``{"vars": {...}}``); see :meth:`pyfsr.api.playbooks.PlaybooksAPI.run_env`
        to build it from a real run.

        Returns the unwrapped ``result`` (a scalar, list, or dict). Use
        :meth:`render_raw` for the full response envelope.
        """
        resp = self.render_raw(template, values)
        if isinstance(resp, dict) and "result" in resp:
            return resp["result"]
        return resp

    def render_raw(self, template: str, values: dict[str, Any] | None = None) -> Any:
        """Render a template and return the raw server response (``{"result": ...}``)."""
        return self.client.post(
            "/api/wf/api/jinja-editor/",
            data={"template": template, "values": values or {}},
        )

    # ------------------------------------------------------- global variables
    def dynamic_variables(self) -> list[dict[str, Any]]:
        """List every FortiSOAR global ("dynamic") variable.

        ``GET /api/wf/api/dynamic-variable/`` -- returns
        ``[{id, name, value, default_value}, ...]`` (referenced in playbooks as
        ``{{ globalVars.<name> }}``).

        Example:
            >>> client = demo_client()
            >>> [v["name"] for v in client.wf_tools.dynamic_variables()]
            ['Default_Indicator_TTL_Days', 'Demo_mode', 'Default_Email']
        """
        resp = self.client.get("/api/wf/api/dynamic-variable/", params={"offset": 0, "limit": 2147483647})
        return extract_members(resp)

    def dynamic_variable(self, name: str) -> str | None:
        """Return the value of one global variable by ``name`` (``None`` if absent)."""
        for v in self.dynamic_variables():
            if v.get("name") == name:
                return v.get("value")
        return None

    def dynamic_variable_record(self, name: str) -> dict[str, Any] | None:
        """Return the whole record for one global variable, or ``None``.

        :meth:`dynamic_variable` gives only the value; the ``id`` is what the
        update and delete routes key on, so callers that write need this.

        Example:
            >>> client = demo_client()
            >>> client.wf_tools.dynamic_variable_record("Demo_mode")["id"]
            9
        """
        for v in self.dynamic_variables():
            if v.get("name") == name:
                return v
        return None

    def set_dynamic_variable(self, name: str, value: str) -> dict[str, Any]:
        """Create a global variable, or update it in place if it already exists.

        ``POST /api/wf/api/dynamic-variable/`` to create,
        ``PUT /api/wf/api/dynamic-variable/{id}/`` to update -- there is no
        upsert route, and POSTing a name that already exists is an error, so the
        current value is looked up first.

        The trailing slash on the update is mandatory; without it the gateway
        rejects the call.

        Returns the appliance's response record for the created or updated
        variable.

        Example:
            >>> client = demo_client()
            >>> client.wf_tools.set_dynamic_variable("Demo_mode", "true")["value"]
            'true'
        """
        existing = self.dynamic_variable_record(name)
        body = {"name": name, "value": value}
        if existing is None:
            resp = self.client.post("/api/wf/api/dynamic-variable/", data=body)
        else:
            resp = self.client.put(f"/api/wf/api/dynamic-variable/{existing['id']}/", data=body)
        return resp if isinstance(resp, dict) else {"name": name, "value": value}

    def delete_dynamic_variable(self, name: str, *, missing_ok: bool = False) -> bool:
        """Delete a global variable by ``name``. Returns whether one was deleted.

        ``DELETE /api/wf/api/dynamic-variable/{id}/`` -- the route keys on the
        id, so the name is resolved first. The trailing slash is mandatory.

        Playbooks that keep an ingestion watermark in a global variable name it
        after their own uuid, so cloning or re-running a data-ingestion wizard
        leaves the previous variable behind pointing at a playbook that no
        longer exists. Deleting one is how such a watermark gets reset.

        Example:
            >>> client = demo_client()
            >>> client.wf_tools.delete_dynamic_variable("Demo_mode")
            True
            >>> client.wf_tools.delete_dynamic_variable("absent", missing_ok=True)
            False

        Raises:
            ValueError: if no variable of that name exists and ``missing_ok``
                is false.
        """
        existing = self.dynamic_variable_record(name)
        if existing is None:
            if missing_ok:
                return False
            raise ValueError(f"no global variable named {name!r}")
        self.client.delete(f"/api/wf/api/dynamic-variable/{existing['id']}/")
        return True
