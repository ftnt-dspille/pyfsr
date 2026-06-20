"""Workflow-engine authoring helpers: Jinja rendering and global variables.

Wraps the two ``/api/wf/api`` endpoints used when authoring/debugging a
playbook outside the visual editor. Accessed as ``client.wf_tools``.

Example:
    >>> client.wf_tools.render("{{ vars.x + 2 }}", {"vars": {"x": 5}})
    7
    >>> client.wf_tools.dynamic_variable("Default_Indicator_TTL_Days")
    '20'
"""

from __future__ import annotations

from typing import Any

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

        ``GET /api/wf/api/dynamic-variable/`` — returns
        ``[{id, name, value, default_value}, ...]`` (referenced in playbooks as
        ``{{ globalVars.<name> }}``).
        """
        resp = self.client.get("/api/wf/api/dynamic-variable/", params={"offset": 0, "limit": 2147483647})
        return (resp or {}).get("hydra:member") or []

    def dynamic_variable(self, name: str) -> str | None:
        """Return the value of one global variable by ``name`` (``None`` if absent)."""
        for v in self.dynamic_variables():
            if v.get("name") == name:
                return v.get("value")
        return None
