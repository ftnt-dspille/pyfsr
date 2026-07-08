"""Typed models for the FortiAI agentic-service surface (``client.ai``).

These wrap the ``fsr-ai`` service responses (``/api/ai/...``) and the
``MCPConfiguration`` module (``/api/3/mcp_configurations``) — distinct from the
installable-package models in :mod:`pyfsr.models._ai_agent_package`. Shapes are
live-verified against a FortiSOAR 8.0 appliance with the FortiAI solution pack
installed; unknown keys are preserved (``extra="allow"``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .base import BaseRecord


class _Lenient(BaseModel):
    """Base for fsr-ai response shapes: not module records (no ``@id``), but
    still dict-compatible so existing ``.get(...)``-style call sites keep working.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, self.model_extra.get(key, default) if self.model_extra else default)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        if self.model_extra and key in self.model_extra:
            return self.model_extra[key]
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return hasattr(self, key) or bool(self.model_extra and key in self.model_extra)


class MCPServerConfig(BaseRecord):
    """A registered MCP server (``/api/3/mcp_configurations`` — the ``MCPConfiguration`` module).

    ``authentication`` is stored server-side as a JSON *string* (e.g.
    ``'{"type":"FSR"}'`` for built-ins, ``'{"value": "<bearer token>"}'`` for a
    remote server) — left untyped since its shape varies by ``type``. See
    :meth:`~pyfsr.api.ai.AIApi.register_mcp_server` for the encode-on-write
    convenience and :meth:`~pyfsr.api.ai.AIApi.mcp_tool_catalog` for decoding it
    back to probe ``tools/list``.
    """

    name: str | None = None
    url: str | None = None
    transport: str | None = None
    type: str | None = None
    active: bool | None = None
    timeout: int | None = None
    command: str | None = None
    authentication: str | dict[str, Any] | None = None
    description: str | None = None
    metadata: Any | None = None


class MCPServerRef(_Lenient):
    """One entry from ``GET /api/ai/mcp`` — the id+name the agent-config UI lists.

    Thinner than :class:`MCPServerConfig` (no url/transport/auth); resolve to
    the full record via :meth:`~pyfsr.api.ai.AIApi.mcp_configs`.
    """

    id: str | None = None
    name: str | None = None


class MCPTool(_Lenient):
    """One tool advertised by an MCP server's ``tools/list`` (from validate/probe)."""

    name: str | None = None
    description: str | None = None
    inputSchema: dict[str, Any] | None = None


class MCPValidateResult(_Lenient):
    """Response of ``POST /api/ai/mcp/validate`` — probing a server before saving."""

    valid: bool = False
    tools: list[MCPTool] = Field(default_factory=list)
    message: str | None = None


class AgentRecord(_Lenient):
    """One installed AI agent (``GET /api/ai/agent/`` / ``GET .../{name}/{version}``)."""

    id: int | None = None
    uuid: str | None = None
    name: str | None = None
    label: str | None = None
    version: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    category: str | None = None
    active: bool | None = None
    status: str | None = None
    classpath: str | None = None
    system: bool | None = None
    installed: bool | None = None
    inputformat: dict[str, Any] = Field(default_factory=dict)
    outputformat: dict[str, Any] = Field(default_factory=dict)
    config_schema: Any | None = None
    configuration: list[Any] = Field(default_factory=list)
    prompt: Any | None = None
    additional_information: list[dict[str, Any]] = Field(default_factory=list)
    config_count: int | None = None
    dependencies: list[Any] = Field(default_factory=list)
    jailbreakguard: bool | None = None
    llmconfig: Any | None = None
    piimasking: bool | None = None


class AgentConfig(_Lenient):
    """The inner ``config`` of an :class:`AgentConfigDTO`.

    ``mcp_server`` is the per-agent MCP-server allowlist (uuids); an agent left
    on the default config reports ``config_type == "default"``.
    """

    config_type: str | None = None
    llm_provider: str | None = None
    mcp_server: list[str] = Field(default_factory=list)
    masking_agent: str | None = None


class AgentConfigDTO(_Lenient):
    """``AiAgentConfigurationDTO`` — response of the agent-config endpoints
    (``GET/POST /api/ai/agent/config/{name}/{version}`` and ``.../default``).
    """

    agent_name: str | None = None
    agent_version: str | None = None
    name: str | None = None
    default: bool = False
    config: AgentConfig = Field(default_factory=AgentConfig)
    config_id: str | None = None


class LLMProvider(_Lenient):
    """An allowed LLM provider — an installed solution pack (``/api/ai/llm/allowed-providers``)."""

    uuid: str | None = None
    name: str | None = None
    label: str | None = None
    version: str | None = None


class LLMConfig(_Lenient):
    """A reasoning-profile config (``GET /api/ai/llm/config``), e.g. *Low Reasoning*.

    ``config.connector_name``/``connector_config_id`` point at the connector
    configuration backing this profile (e.g. the ``fortinet-fortiai-proxy`` proxy).
    """

    uuid: str | None = None
    name: str | None = None
    isdefault: bool | None = None
    active: bool | None = None
    model: str | None = None
    modelname: str | None = None
    provider: str | None = None
    apikey: str | None = None
    baseurl: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class InvestigationHandle(_Lenient):
    """Response of starting/triggering a triage run — ``{"task_id", "status"}``."""

    task_id: str | None = None
    status: str | None = None


class InvestigationResult(_Lenient):
    """Full triage result/verdict (``GET /api/ai/agents/{task_id}/result``).

    ``summary``/``hypotheses``/``logs`` are left untyped (``Any``) — see
    :meth:`~pyfsr.api.ai.AIApi.investigation_questions` and
    :meth:`~pyfsr.api.ai.AIApi.hypothesis_evidence` for the derived, typed views
    over this payload.
    """

    task_id: str | None = None
    status: str | None = None
    summary: dict[str, Any] | None = None
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[dict[str, Any]] = Field(default_factory=list)


class InvestigationQuestion(_Lenient):
    """One question/evidence entry — see :meth:`~pyfsr.api.ai.AIApi.investigation_questions`."""

    index: int | None = None
    question: str | None = None
    agent: str | None = None
    input: Any | None = None
    response: Any | None = None
    evidence: str | None = None
    supports: list[str] = Field(default_factory=list)
    weakens: list[str] = Field(default_factory=list)
    information_type: Any | None = None
    status: str | None = None


class ConnectorMcpCandidates(_Lenient):
    """Which installed connectors can be hosted as an MCP server (``GET /mcp/servers/connector``).

    ``restricted`` connectors (internal/system ones, e.g. the agent-communication
    bridge) can never be hosted. ``available`` connectors aren't yet hosted —
    once one is, it drops off this list (find it instead via :meth:`~pyfsr.api.ai.AIApi.mcp_configs`,
    filtering on ``type == "connector"``).
    """

    available: list[str] = Field(default_factory=list)
    restricted: list[str] = Field(default_factory=list)


class ToolCall(_Lenient):
    """One MCP/connector tool invocation, from ``llm_activity_logs`` — see
    :meth:`~pyfsr.api.ai.AIApi.tool_usage`."""

    tool_name: str | None = None
    tool_args: Any | None = None
    correlation_id: str | None = None
    title: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    server: str | None = None
    server_uuid: str | None = None
