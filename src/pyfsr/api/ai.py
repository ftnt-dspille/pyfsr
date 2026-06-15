"""FortiAI — agentic alert investigation, LLM-provider and MCP-server management.

FortiSOAR 8.0 ships an on-appliance agentic AI service (``fsr-ai``) mounted at
``/api/ai``. This module wraps the three things you actually drive from a
client:

- **Investigation** — fire the multi-agent triage pipeline at an alert
  (normalize → hypothesize → plan → gather evidence over MCP → verdict) and poll
  for the result. See :meth:`AIApi.investigate_alert`.
- **LLM providers** — list the configured reasoning profiles and the
  provider/model catalogue (``/api/ai/llm``).
- **MCP servers** — list, validate and register the Model Context Protocol
  servers the investigation agents are allowed to call (``/api/ai/mcp`` +
  the ``mcp_configurations`` collection).

Plus the one-time enablement gate: FortiAI features must be turned on (and the
AI terms accepted) in System Settings before any of this works — see
:meth:`AIApi.enable_features`, which writes ``publicValues.ai_feature``.

Accessed as ``client.ai``.

Example:
    >>> client = FortiSOAR("soar.example.com", api_key)
    >>> client.ai.enable_features()                       # one-time, accepts AI T&C
    >>> report = client.ai.investigate_alert("alerts:740a751c-...", wait=True)
    >>> report["status"]
    'completed'

Endpoint reference (verified against FSR 8.0 ``fsr-ai``):

================================================  ==================================================
Operation                                         Endpoint
================================================  ==================================================
start investigation                               ``POST /api/ai/triage/alert``
poll status                                       ``GET  /api/ai/agents/{task_id}/status``
fetch result/verdict                              ``GET  /api/ai/agents/{task_id}/result``
run one agent                                     ``POST /api/ai/triage/{agent_name}/trigger``
submit verdict feedback                           ``POST /api/ai/agents/{task_id}/acceptance``
list reasoning profiles                           ``GET  /api/ai/llm/config``
list providers                                    ``GET  /api/ai/llm/allowed-providers``
list MCP servers                                  ``GET  /api/ai/mcp``
validate an MCP server config                     ``POST /api/ai/mcp/validate``
register an MCP server                            ``POST /api/3/mcp_configurations``
================================================  ==================================================
"""

from __future__ import annotations

import json
import time
from typing import Any

from .base import BaseAPI

#: Triage/agent statuses that mean the pipeline has stopped running.
TERMINAL_STATUSES = frozenset({"completed", "failed", "error", "cancelled"})


class AIApi(BaseAPI):
    """Drive the FortiAI agentic investigation service and its configuration."""

    # ----------------------------------------------------------- enablement
    def features_enabled(self) -> bool:
        """Return whether FortiAI features are enabled in System Settings.

        Reads ``publicValues.ai_feature.enable`` from the root settings record.
        """
        ai = (self.client.system_settings.get_public_values() or {}).get("ai_feature") or {}
        return bool(ai.get("enable"))

    def enable_features(
        self, enabled: bool = True, *, modified_by: str | None = None
    ) -> dict[str, Any]:
        """Enable (or disable) FortiAI features — the AI terms-acceptance gate.

        This is the programmatic equivalent of toggling *Enable AI Features* in
        **System Settings**; FortiSOAR records it as
        ``publicValues.ai_feature`` and treats enabling it as acceptance of the
        AI terms & conditions. Must be done once before any investigation,
        LLM-config or MCP call will succeed.

        Args:
            enabled: ``True`` to turn features on (default), ``False`` to disable.
            modified_by: optional display name stamped as ``lastModifiedBy``.

        Returns:
            The updated root ``SystemSettings`` record.
        """
        patch: dict[str, Any] = {"ai_feature": {"enable": bool(enabled)}}
        if modified_by:
            patch["ai_feature"]["lastModifiedBy"] = modified_by
        return self.client.system_settings.update(patch)

    # ----------------------------------------------------------- investigation
    def start_alert_investigation(self, alert: dict[str, Any] | str) -> dict[str, Any]:
        """Kick off an asynchronous AI investigation of one alert.

        ``alert`` may be the full alert record (a dict, as returned by
        ``client.alerts.get(...)``) or a record reference (``"<uuid>"``,
        ``"alerts:<uuid>"`` or a full ``/api/3/alerts/<uuid>`` IRI), in which
        case the record is fetched first. The whole alert JSON is posted to the
        triage pipeline.

        Returns the pipeline handle ``{"task_id": ..., "status": "pending"}``.
        Poll it with :meth:`get_status` / :meth:`get_result`, or pass the alert
        to :meth:`investigate_alert` to do both in one call.
        """
        if isinstance(alert, str):
            alert = self._fetch_alert(alert)
        return self.client.post("/api/ai/triage/alert", data=alert)

    def get_status(self, task_id: str) -> str:
        """Return the current pipeline status for a triage task.

        One of ``"running"``, ``"completed"`` or ``"failed"`` (see
        :data:`TERMINAL_STATUSES`).
        """
        resp = self.client.get(f"/api/ai/agents/{task_id}/status")
        return (resp or {}).get("status", "") if isinstance(resp, dict) else ""

    def get_result(self, task_id: str) -> dict[str, Any]:
        """Fetch the full investigation result/verdict for a triage task.

        The payload carries the per-stage ``phases`` (normalization →
        hypothesis → planning → evidence → verdict), any ``logs``, and — once
        ``status == "completed"`` — the synthesized verdict/summary. While the
        pipeline is still running this returns the partial progress so far.
        """
        resp = self.client.get(f"/api/ai/agents/{task_id}/result")
        return resp if isinstance(resp, dict) else {"result": resp}

    def wait_for_result(
        self, task_id: str, *, interval: float = 5.0, timeout: float = 600.0
    ) -> dict[str, Any]:
        """Poll a triage task until it reaches a terminal status, then return it.

        Args:
            task_id: the id from :meth:`start_alert_investigation`.
            interval: seconds between status polls (default 5).
            timeout: give up after this many seconds (default 600 / 10 min).

        Returns:
            The :meth:`get_result` payload, with a top-level ``status`` key. On
            timeout, returns the latest result with ``status`` left non-terminal
            rather than raising.
        """
        deadline = time.monotonic() + timeout
        status = self.get_status(task_id)
        while status not in TERMINAL_STATUSES and time.monotonic() < deadline:
            time.sleep(interval)
            status = self.get_status(task_id)
        result = self.get_result(task_id)
        result.setdefault("status", status)
        return result

    def investigate_alert(
        self,
        alert: dict[str, Any] | str,
        *,
        wait: bool = False,
        interval: float = 5.0,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Start an investigation and (optionally) block until it finishes.

        Convenience over :meth:`start_alert_investigation` +
        :meth:`wait_for_result`. With ``wait=False`` (default) returns the
        ``{"task_id", "status"}`` handle immediately; with ``wait=True`` polls
        and returns the final result payload (including its ``task_id``).
        """
        started = self.start_alert_investigation(alert)
        task_id = started.get("task_id")
        if not wait or not task_id:
            return started
        result = self.wait_for_result(task_id, interval=interval, timeout=timeout)
        result.setdefault("task_id", task_id)
        return result

    def run_agent(self, agent_name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Trigger a single named agent (e.g. ``"ioc-enrichment"``) directly.

        Returns the ``{"task_id", "status"}`` handle; poll with
        :meth:`get_status` / :meth:`get_result` exactly like an investigation.
        """
        return self.client.post(f"/api/ai/triage/{agent_name}/trigger", data=data)

    def submit_feedback(self, task_id: str, feedback: dict[str, Any]) -> dict[str, Any]:
        """Submit analyst feedback / acceptance on a triage verdict."""
        return self.client.post(f"/api/ai/agents/{task_id}/acceptance", data=feedback)

    # ----------------------------------------------------------- LLM providers
    def list_providers(self) -> list[dict[str, Any]]:
        """List the allowed LLM providers (the installed AI solution packs)."""
        return _as_list(self.client.get("/api/ai/llm/allowed-providers"))

    def list_llm_configs(self) -> list[dict[str, Any]]:
        """List the configured reasoning profiles (e.g. *Low* / *High Reasoning*)."""
        return _as_list(self.client.get("/api/ai/llm/config"))

    def get_llm_config(self, uuid: str) -> dict[str, Any]:
        """Fetch one reasoning-profile config by uuid."""
        resp = self.client.get(f"/api/ai/llm/config/{uuid}")
        return resp if isinstance(resp, dict) else {"config": resp}

    def list_models(self) -> list[dict[str, Any]]:
        """List every model exposed across the configured providers."""
        return _as_list(self.client.get("/api/ai/llm/models"))

    def create_llm_config(self, configs: list[dict[str, Any]]) -> Any:
        """Create one or more reasoning-profile configs (``POST /api/ai/llm/config``).

        The endpoint takes a *list* of config objects, mirroring the UI's bulk
        save; a single dict is wrapped automatically.
        """
        body = configs if isinstance(configs, list) else [configs]
        return self.client.post("/api/ai/llm/config", data=body)

    def test_llm_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Live-test an LLM config without persisting it (``POST /api/ai/llm/test``)."""
        resp = self.client.post("/api/ai/llm/test", data=config)
        return resp if isinstance(resp, dict) else {"result": resp}

    def delete_llm_config(self, uuid: str) -> None:
        """Delete a reasoning-profile config by uuid."""
        self.client.delete(f"/api/ai/llm/config/{uuid}")

    # ----------------------------------------------------------- MCP servers
    def list_mcp_servers(self) -> list[dict[str, Any]]:
        """List registered MCP servers (id + name) the AI agents can be granted."""
        return _as_list(self.client.get("/api/ai/mcp"))

    def validate_mcp_server(self, config: dict[str, Any]) -> dict[str, Any]:
        """Probe an MCP-server config *before* persisting it.

        Opens a connection to the server's ``url`` and runs ``tools/list``,
        returning ``{"valid": bool, "tools": [...], "message": ...}``. Inspect
        ``tools`` for the names you'll later allowlist per agent. Always call
        this before :meth:`register_mcp_server` and do not persist on failure.
        """
        resp = self.client.post("/api/ai/mcp/validate", data=config)
        return resp if isinstance(resp, dict) else {"result": resp}

    def register_mcp_server(self, config: dict[str, Any]) -> dict[str, Any]:
        """Persist a validated MCP-server config (``POST /api/3/mcp_configurations``).

        The ``authentication`` block is encrypted server-side, so always create
        rows through this API — never write the ``mcp_configuration`` table
        directly. Returns the created record (including its new ``uuid``).

        Note: the persistence layer stores ``authentication`` as a JSON *string*
        (the built-ins are e.g. ``'{"type":"FSR"}'``). A dict is JSON-encoded here
        automatically — passing a raw object makes the backend stringify it to the
        literal ``"Array"``, which then breaks ``GET /api/ai/mcp/status`` for every
        server (it ``json.loads`` each row's auth).
        """
        config = dict(config)
        auth = config.get("authentication")
        if isinstance(auth, dict):
            config["authentication"] = json.dumps(auth)
        return self.client.post("/api/3/mcp_configurations", data=config)

    def delete_mcp_server(self, uuid: str) -> None:
        """Delete a registered MCP server by uuid."""
        self.client.delete(f"/api/3/mcp_configurations/{uuid}")

    # ----------------------------------------------------------- internals
    def _fetch_alert(self, ref: str) -> dict[str, Any]:
        """Resolve a record reference to the full alert JSON for triage."""
        uuid = ref.rstrip("/").split("/")[-1].split(":")[-1]
        return self.client.alerts.get(uuid)


def _as_list(resp: Any) -> list[dict[str, Any]]:
    """Coerce a FortiAI response into a list (handles bare lists + Hydra)."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        return resp.get("hydra:member") or resp.get("data") or [resp]
    return []
