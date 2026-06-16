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
update a registered MCP server                    ``PUT  /api/3/mcp_configurations/{uuid}``
list AI agents                                    ``GET  /api/ai/agent/``
get one AI agent                                  ``GET  /api/ai/agent/{name}/{version}``
get an agent's configuration                      ``GET  /api/ai/agent/config/{name}/{version}``
update an agent's configuration                   ``POST /api/ai/agent/config``
get/update the default agent configuration        ``GET/POST /api/ai/agent/config/default``
activate/deactivate agents                        ``POST /api/ai/agent/activate``
================================================  ==================================================

Agent ↔ MCP binding
-------------------
Which MCP servers a triage agent may call is stored on the agent's
configuration as ``config["mcp_server"]`` — a list of registered MCP-server
UUIDs. To let an agent reach a newly-registered server (e.g. FortiSIEM), append
its uuid to that list and ``PUT`` the config back; the high-level
:meth:`AIApi.allow_mcp_server_for_agent` does the read-modify-write for you.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .base import BaseAPI

#: Triage/agent statuses that mean the pipeline has stopped running.
TERMINAL_STATUSES = frozenset({"completed", "failed", "error", "cancelled"})

#: Key on an agent's ``config`` dict holding the list of allowed MCP-server UUIDs.
AGENT_CONFIG_MCP_KEY = "mcp_server"


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

    def update_mcp_server(self, uuid: str, config: dict[str, Any]) -> dict[str, Any]:
        """Update a registered MCP server (``PUT /api/3/mcp_configurations/{uuid}``).

        Use this to rotate a credential whose token expires — e.g. re-stamping a
        FortiSIEM ``bearer`` value after minting a fresh OAuth token (FortiSOAR's
        MCP client only forwards a *static* credential; it does not run the
        OAuth ``client_credentials`` grant itself, so the token must be refreshed
        out-of-band and written back here).

        ``authentication`` is JSON-encoded for you exactly as in
        :meth:`register_mcp_server`. As the UI does, ``uuid`` is dropped from the
        request body (it goes in the URL, not the payload).
        """
        config = dict(config)
        config.pop("uuid", None)  # UI deletes uuid from the body on PUT
        auth = config.get("authentication")
        if isinstance(auth, dict):
            config["authentication"] = json.dumps(auth)
        return self.client.put(f"/api/3/mcp_configurations/{uuid}", data=config)

    def save_mcp_server(self, config: dict[str, Any], *, validate: bool = True) -> dict[str, Any]:
        """Validate then persist an MCP server — the exact flow the FortiSOAR UI uses.

        The UI gates *Save* on a successful *Test*, so this mirrors it: it first
        calls :meth:`validate_mcp_server` (with ``authentication`` as an object)
        and refuses to persist on failure, then creates or updates the record
        (``authentication`` JSON-encoded). If ``config`` carries a ``uuid`` it
        updates that row (``PUT``); otherwise it creates a new one (``POST``).

        Pass ``validate=False`` to skip the probe (e.g. re-saving a server whose
        token can't be re-validated from a stripped UI form).

        Returns the persisted record.
        """
        if validate:
            result = self.validate_mcp_server(config)
            if not result.get("valid"):
                raise ValueError(
                    f"MCP server did not validate, not saving: {result.get('message') or result}"
                )
        uuid = config.get("uuid")
        if uuid:
            return self.update_mcp_server(uuid, config)
        return self.register_mcp_server(config)

    def delete_mcp_server(self, uuid: str) -> None:
        """Delete a registered MCP server by uuid."""
        self.client.delete(f"/api/3/mcp_configurations/{uuid}")

    # ----------------------------------------------------------- agents
    def list_agents(self, **filters: Any) -> list[dict[str, Any]]:
        """List the installed AI agents (``GET /api/ai/agent/``).

        Optional keyword filters are passed straight through as query params —
        the service recognizes ``category``, ``status``, ``active``,
        ``installed``, ``system`` and ``publisher``. Each item is an agent
        record with ``name``, ``version``, ``label``, ``uuid``, ``active`` etc.
        """
        params = {k: v for k, v in filters.items() if v is not None}
        return _as_list(self.client.get("/api/ai/agent/", params=params or None))

    def get_agent(self, name: str, version: str) -> dict[str, Any]:
        """Fetch one AI agent's details (``GET /api/ai/agent/{name}/{version}``)."""
        resp = self.client.get(f"/api/ai/agent/{name}/{version}")
        return resp if isinstance(resp, dict) else {"agent": resp}

    def get_agent_config(self, name: str, version: str) -> dict[str, Any]:
        """Fetch an agent's configuration (``GET /api/ai/agent/config/{name}/{version}``).

        Returns the ``AiAgentConfigurationDTO`` shape::

            {"agent_name", "agent_version", "name", "default",
             "config": {"config_type", "llm_provider",
                        "mcp_server": [<uuid>, ...], "masking_agent"},
             "config_id"}

        The ``config["mcp_server"]`` list is the per-agent allowlist of MCP
        servers the agent may call. An agent on the *default* config reports
        ``config["config_type"] == "default"``.
        """
        resp = self.client.get(f"/api/ai/agent/config/{name}/{version}")
        return resp if isinstance(resp, dict) else {"config": resp}

    def update_agent_config(
        self,
        agent_name: str,
        agent_version: str,
        config: dict[str, Any],
        *,
        name: str | None = None,
        config_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist an agent's configuration (``POST /api/ai/agent/config``).

        ``config`` is the inner config dict (``llm_provider``, ``mcp_server``,
        ``masking_agent`` …). Prefer the higher-level
        :meth:`allow_mcp_server_for_agent` when all you want is to grant the
        agent one more MCP server.

        Note: this is a ``POST`` even though it updates — fsr-ai's ``POST
        /config`` handler upserts, and (importantly) the FortiSOAR API gateway
        only authorizes ``POST ^agent/config$`` against ``update.ai_agents``; a
        ``PUT`` matches no ACL rule and is rejected with ``Access Denied``.
        """
        body: dict[str, Any] = {
            "agent_name": agent_name,
            "agent_version": agent_version,
            "config": config,
        }
        if name is not None:
            body["name"] = name
        if config_id is not None:
            body["config_id"] = config_id
        return self.client.post("/api/ai/agent/config", data=body)

    def get_default_agent_config(self) -> dict[str, Any]:
        """Fetch the default agent configuration (``GET /api/ai/agent/config/default``)."""
        resp = self.client.get("/api/ai/agent/config/default")
        return resp if isinstance(resp, dict) else {"config": resp}

    def update_default_agent_config(
        self, config: dict[str, Any], *, name: str | None = None
    ) -> dict[str, Any]:
        """Update the default agent configuration (``POST /api/ai/agent/config/default``).

        Agents left on the default config inherit this ``mcp_server`` list, so
        appending a uuid here grants the server to *every* such agent at once.
        """
        body: dict[str, Any] = {"config": config, "default": True}
        if name is not None:
            body["name"] = name
        return self.client.post("/api/ai/agent/config/default", data=body)

    def activate_agent(self, uuids: list[str], *, active: bool = True) -> dict[str, Any]:
        """Activate or deactivate agents by uuid (``POST /api/ai/agent/activate``)."""
        return self.client.post(
            "/api/ai/agent/activate", data={"uuids": uuids}, params={"active": active}
        )

    # -------------------------------------------------- agent ↔ MCP binding
    def mcp_server_names(self) -> dict[str, str]:
        """Return a ``{uuid: name}`` map of every registered MCP server.

        Handy for turning an agent's raw ``mcp_server`` UUID allowlist into
        human-readable names — see :meth:`list_agent_mcp_servers` with
        ``friendly=True`` and :meth:`describe_agent_mcp_servers`.
        """
        return {
            (m.get("id") or m.get("uuid")): m.get("name")
            for m in self.list_mcp_servers()
            if (m.get("id") or m.get("uuid"))
        }

    def list_agent_mcp_servers(
        self, name: str, version: str, *, friendly: bool = False
    ) -> list[str]:
        """Return the MCP servers an agent is currently allowed to call.

        By default returns the raw server UUIDs as stored on the agent config.
        Pass ``friendly=True`` to get the registered server *names* instead
        (unknown/unregistered UUIDs are returned unchanged).
        """
        config = self.get_agent_config(name, version).get("config") or {}
        uuids = list(config.get(AGENT_CONFIG_MCP_KEY) or [])
        if not friendly:
            return uuids
        names = self.mcp_server_names()
        return [names.get(u, u) for u in uuids]

    def describe_agent_mcp_servers(self, name: str, version: str) -> list[dict[str, str]]:
        """Return the agent's allowed MCP servers as ``[{"uuid", "name"}, ...]``.

        Pairs each allowlisted UUID with its registered name (``name`` falls
        back to the UUID for anything not currently registered).
        """
        config = self.get_agent_config(name, version).get("config") or {}
        uuids = list(config.get(AGENT_CONFIG_MCP_KEY) or [])
        names = self.mcp_server_names()
        return [{"uuid": u, "name": names.get(u, u)} for u in uuids]

    def allow_mcp_server_for_agent(self, name: str, version: str, mcp_uuid: str) -> dict[str, Any]:
        """Grant one agent access to an MCP server (read-modify-write of its config).

        Appends ``mcp_uuid`` to the agent's ``config["mcp_server"]`` allowlist
        (no-op if already present) and PUTs the config back. If the agent is on
        the *default* config it is forked into its own config first, seeded from
        the default, so other agents are unaffected.

        Returns the updated ``AiAgentConfigurationDTO``. Takes effect on the next
        investigation — no service restart required.
        """
        dto = self.get_agent_config(name, version) or {}
        config = dict(dto.get("config") or {})
        # An agent reported as "default" has no row of its own yet — seed from
        # the default config so the write creates a dedicated, non-shared row.
        if config.get("config_type") == "default" or not config:
            config = dict((self.get_default_agent_config().get("config")) or {})
            config.pop("config_type", None)
        allowed = list(config.get(AGENT_CONFIG_MCP_KEY) or [])
        if mcp_uuid not in allowed:
            allowed.append(mcp_uuid)
        config[AGENT_CONFIG_MCP_KEY] = allowed
        return self.update_agent_config(
            name, version, config, name=dto.get("name"), config_id=dto.get("config_id")
        )

    def disallow_mcp_server_for_agent(
        self, name: str, version: str, mcp_uuid: str
    ) -> dict[str, Any]:
        """Revoke an agent's access to an MCP server (inverse of
        :meth:`allow_mcp_server_for_agent`)."""
        dto = self.get_agent_config(name, version) or {}
        config = dict(dto.get("config") or {})
        allowed = [u for u in (config.get(AGENT_CONFIG_MCP_KEY) or []) if u != mcp_uuid]
        config[AGENT_CONFIG_MCP_KEY] = allowed
        return self.update_agent_config(
            name, version, config, name=dto.get("name"), config_id=dto.get("config_id")
        )

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
