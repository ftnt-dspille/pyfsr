"""Typed models for the FortiAI agentic-service surface (``client.ai``).

These wrap the ``fsr-ai`` service responses (``/api/ai/...``) and the
``MCPConfiguration`` module (``/api/3/mcp_configurations``) -- distinct from the
installable-package models in :mod:`pyfsr.models._ai_agent_package`. Shapes are
live-verified against a FortiSOAR 8.0 appliance with the FortiAI solution pack
installed; unknown keys are preserved (``extra="allow"``).
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

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
    """A registered MCP server (``/api/3/mcp_configurations`` -- the ``MCPConfiguration`` module).

    ``authentication`` is stored server-side as a JSON *string* (e.g.
    ``'{"type":"FSR"}'`` for built-ins, ``'{"value": "<bearer token>"}'`` for a
    remote server) -- left untyped since its shape varies by ``type``. See
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
    """One entry from ``GET /api/ai/mcp`` -- the id+name the agent-config UI lists.

    Thinner than :class:`MCPServerConfig` (no url/transport/auth); resolve to
    the full record via :meth:`~pyfsr.api.ai.AIApi.mcp_configs`.
    """

    id: str | None = None
    name: str | None = None


class MCPTool(_Lenient):
    """One tool advertised by an MCP server's ``tools/list``.

    Used both by the *registration* probe (:meth:`~pyfsr.api.ai.AIApi.validate_mcp_server`,
    which reads the MCP-native ``inputSchema`` key) and by the appliance's own
    native gateway (:meth:`~pyfsr.api.native_mcp.NativeMCPApi.list_tools`, whose
    historical dict shape used ``input_schema``). ``inputSchema`` accepts either
    spelling on the wire and :attr:`input_schema` reads it back either way, so
    ``tool["input_schema"]``/``tool.get("input_schema")`` and ``tool.inputSchema``
    all resolve -- the dict-style access the tool-surface materializer relies on
    keeps working.
    """

    name: str | None = None
    description: str | None = None
    inputSchema: dict[str, Any] | None = Field(
        default=None, validation_alias=AliasChoices("inputSchema", "input_schema")
    )

    @property
    def input_schema(self) -> dict[str, Any] | None:
        """Snake-case alias for :attr:`inputSchema` (native-gateway dict shape)."""
        return self.inputSchema


class MCPToolResult(_Lenient):
    """The ``{"status", "result", "error"}`` envelope a native gateway tool returns.

    Every FortiSOAR native tool (``/mcp/soc/``, ``/mcp/playbooks/``, ...) replies
    with this envelope on success; :attr:`ok` is a convenience for
    ``status == "success"``. In-band tool failures come back as a plain string
    instead of this envelope -- :meth:`~pyfsr.api.native_mcp.NativeMCPApi.call_tool`
    returns that raw value untouched, while
    :meth:`~pyfsr.api.native_mcp.NativeMCPApi.call_tool_result` always wraps into
    this model (a non-envelope payload lands under :attr:`result` with
    ``status=None``). Extra keys are preserved (``extra="allow"``).
    """

    status: str | None = None
    result: Any = None
    error: Any = None

    @property
    def ok(self) -> bool:
        """``status == "success"`` -- the FortiSOAR-native envelope convention.

        Only meaningful for FortiSOAR's own tools (native gateway / internal
        registered servers), which reply with ``{"status": "success", ...}``. A
        third-party MCP server returns its own payload shape (e.g. raw text or its
        own JSON), so ``ok`` is ``False`` even on success -- read :attr:`result` /
        :attr:`error` for those.
        """
        return self.status == "success"


class MCPValidateResult(_Lenient):
    """Response of ``POST /api/ai/mcp/validate`` -- probing a server before saving."""

    valid: bool = False
    tools: list[MCPTool] = Field(default_factory=list)
    message: str | None = None


class MCPServerStatus(_Lenient):
    """One entry from ``GET /api/ai/mcp/status`` -- the health of a registered MCP server.

    Complements :class:`MCPServerRef` (id+name) and :class:`MCPServerConfig`
    (full record): this is the runtime liveness probe the agent UI uses to show
    green/red per server. ``valid`` is the connectivity verdict; ``error``
    carries the failure reason when it is not.
    """

    uuid: str | None = None
    name: str | None = None
    valid: bool = False
    error: str | None = None


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

    name: str | None = None
    config_type: str | None = None
    llm_provider: str | None = None
    mcp_server: list[str] = Field(default_factory=list)
    masking_agent: str | None = None


class AgentConfigDTO(_Lenient):
    """``AiAgentConfigurationDTO`` -- response of the agent-config endpoints
    (``GET/POST /api/ai/agent/config/{name}/{version}`` and ``.../default``).
    """

    agent_name: str | None = None
    agent_version: str | None = None
    name: str | None = None
    default: bool = False
    config: AgentConfig = Field(default_factory=AgentConfig)
    config_id: str | None = None


class LLMProvider(_Lenient):
    """An allowed LLM provider -- an installed solution pack (``/api/ai/llm/allowed-providers``)."""

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


class FortiAITokenBalance(_Lenient):
    """FortiAI token entitlement for this appliance -- see :meth:`~pyfsr.api.ai.AIApi.token_balance`.

    ``entitled_tokens`` is the included allowance (5,000,000 a month on 8.0.x),
    ``remain_tokens`` what is left of it; the ``*_topup_tokens`` pair counts
    purchased top-ups. ``type`` is ``DEVICE_LEVEL`` when the pool belongs to the
    appliance serial ``sn``.
    """

    entitled_tokens: int = 0
    remain_tokens: int = 0
    entitled_topup_tokens: int = 0
    remain_topup_tokens: int = 0
    account_id: int | None = None
    sn: str | None = None
    type: str | None = None

    @property
    def used_tokens(self) -> int:
        """Tokens consumed from the allowance and top-ups so far."""
        return (self.entitled_tokens - self.remain_tokens) + (self.entitled_topup_tokens - self.remain_topup_tokens)

    @property
    def remaining(self) -> int:
        """Tokens left, allowance plus top-ups."""
        return self.remain_tokens + self.remain_topup_tokens


class InvestigationHandle(_Lenient):
    """Response of starting/triggering a triage run -- ``{"task_id", "status"}``."""

    task_id: str | None = None
    status: str | None = None


class InvestigationResult(_Lenient):
    """Full triage result/verdict (``GET /api/ai/agents/{task_id}/result``).

    ``summary``/``hypotheses``/``logs`` are left untyped (``Any``) -- see
    :meth:`~pyfsr.api.ai.AIApi.investigation_questions` and
    :meth:`~pyfsr.api.ai.AIApi.hypothesis_evidence` for the derived, typed views
    over this payload.
    """

    task_id: str | None = None
    status: str | None = None
    summary: dict[str, Any] | None = None
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[dict[str, Any]] = Field(default_factory=list)


class AgentRunResult(_Lenient):
    """Result of one *single-agent* run (:meth:`~pyfsr.api.ai.AIApi.run_agent`).

    A single agent answers one question; it does not run the investigation
    pipeline, so this is a different shape from :class:`InvestigationResult` --
    the keys mirror the agent's own ``outputformat`` (``answer`` / ``evidence`` /
    ``confidence``) rather than ``summary``/``hypotheses``. ``phases`` is present
    but empty on a single-agent run; it is only populated for a full
    investigation. Live-verified on 8.0.
    """

    task_id: str | None = None
    status: str | None = None
    answer: Any | None = None
    evidence: Any | None = None
    confidence: str | None = None
    logs: list[dict[str, Any]] = Field(default_factory=list)
    phases: list[dict[str, Any]] = Field(default_factory=list)


class InvestigationQuestion(_Lenient):
    """One question/evidence entry -- see :meth:`~pyfsr.api.ai.AIApi.investigation_questions`."""

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
    bridge) can never be hosted. ``available`` connectors aren't yet hosted --
    once one is, it drops off this list (find it instead via :meth:`~pyfsr.api.ai.AIApi.mcp_configs`,
    filtering on ``type == "connector"``).
    """

    available: list[str] = Field(default_factory=list)
    restricted: list[str] = Field(default_factory=list)


class ToolCall(_Lenient):
    """One MCP/connector tool invocation of an investigation.

    ``source`` says where it was read from. ``"llm_activity_logs"`` (8.0.0; see
    :meth:`~pyfsr.api.ai.AIApi.tool_usage`) carries only the LLM-selected calls,
    with ``title``/``model``/``latency_ms``. ``"traces"`` (8.0.1+) carries every
    call the agents ran, plus ``agent``/``question`` (what it served),
    ``selected_by`` (``code``/``llm``/``llm-chained``; see
    :class:`TracedToolCall`), ``server`` straight from the trace, and the real
    ``output``/``error``.
    """

    tool_name: str | None = None
    tool_args: Any | None = None
    correlation_id: str | None = None
    title: str | None = None
    model: str | None = None
    latency_ms: int | None = None
    server: str | None = None
    server_uuid: str | None = None
    source: str = "llm_activity_logs"
    agent: str | None = None
    question: str | None = None
    selected_by: str | None = None
    output: Any = None
    error: Any = None
    cached: bool | None = None
    span_id: str | None = None


# ----------------------------------------------------------------- traces (8.0.1)
# Agent traceability -- the ``fsr-ai`` tracer store behind the
# ``aiAgentTraceability`` widget (``/api/ai/traces/...``). Live-verified on 8.0.1.


class TraceSummary(_Lenient):
    """One row of ``GET /api/ai/traces/`` -- a top-level agent run.

    ``start_time``/``end_time`` are ISO-8601 strings; ``end_time`` is ``None``
    while the run is still going (``status == "RUNNING"``). ``span_counts`` is
    per span type (``{"LLM": 2, "TOOL": 1, ...}``).
    """

    trace_id: str
    parent_trace_id: str | None = None
    root_span_id: str | None = None
    name: str | None = None
    status: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    total_tokens: int = 0
    total_cost: float = 0.0
    span_counts: dict[str, int] = Field(default_factory=dict)
    tags: list[Any] = Field(default_factory=list)


class AgentToolResult(_Lenient):
    """fsr-ai's ``ToolResult``: the outcome of one tool call an agent made.

    It is the ``output`` of a ``TOOL`` trace span (:attr:`TraceSpan.tool_result`).
    8.0.1 added ``mcp_server_id``/``mcp_server_name`` (the registered server
    that ran the tool; the built-ins show as e.g. ``"SOC Framework"``),
    ``tool_call_id`` (the LLM's call id, may be ``None``) and ``cached``
    (answered from fsr-ai's tool cache rather than a fresh call). Live-verified
    on 8.0.1.
    """

    name: str | None = None
    status: str | None = None
    args: dict[str, Any] | None = None
    result: Any = None
    error: Any = None
    mcp_server_id: str | None = None
    mcp_server_name: str | None = None
    tool_call_id: str | None = None
    cached: bool = False
    tool_results: list[Any] = Field(default_factory=list)


class TraceSpan(_Lenient):
    """One span (``GET /api/ai/traces/spans/{span_id}`` or a row of ``.../{trace_id}/spans``).

    ``span_type`` is one of ``AGENT``, ``LLM``, ``LLM_WRAPPER``, ``TOOL``,
    ``SKILL_RETRIEVER``, ``DOCUMENT_SEARCH``, ``CUSTOM``. ``input``/``output``
    keep the raw payload: for an ``LLM`` span the prompt is under
    ``input.payload.params.messages`` and the reply under ``output`` (``content``,
    ``tools``, ``usage``, ``provider``, ``model``).
    """

    span_id: str
    trace_id: str | None = None
    parent_span_id: str | None = None
    name: str | None = None
    span_type: str | None = None
    status: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    duration_ms: float | None = None
    input: Any = None
    output: Any = None
    error: Any = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    user_id: str | None = None

    @property
    def tool_result(self) -> AgentToolResult | None:
        """This span's ``output`` as an :class:`AgentToolResult` for a ``TOOL`` span, else ``None``."""
        if self.span_type != "TOOL" or not isinstance(self.output, dict):
            return None
        return AgentToolResult.model_validate(self.output)

    @property
    def usage(self) -> dict[str, Any]:
        """Token usage for an LLM span (``prompt_tokens``/``completion_tokens``/``total_tokens``), else ``{}``."""
        out = self.output if isinstance(self.output, dict) else {}
        return out.get("usage") or {}

    @property
    def provider(self) -> str | None:
        """LLM provider recorded on the span (``output.provider``); ``None`` for non-LLM spans."""
        out = self.output if isinstance(self.output, dict) else {}
        return out.get("provider") or None

    @property
    def model(self) -> str | None:
        """LLM model recorded on the span (``output.model``). Empty on 8.0.1 for FortiAI-proxy calls."""
        out = self.output if isinstance(self.output, dict) else {}
        return out.get("model") or None

    @property
    def tool_call(self) -> dict[str, Any]:
        """``{"name", "args"}`` for a TOOL span (``input.tool_call``), else ``{}``."""
        inp = self.input if isinstance(self.input, dict) else {}
        return inp.get("tool_call") or {}


class TraceNode(_Lenient):
    """A node of a trace tree (``.../{trace_id}``, ``.../execution-tree``, ``.../spans/{id}/tree``).

    Carries a summary of the span (no full I/O -- fetch that with
    :meth:`~pyfsr.api.ai.AITracesAPI.span`) plus its ``children``.
    ``start_offset_ms`` is relative to the tree root.
    """

    span_id: str
    name: str | None = None
    span_type: str | None = None
    status: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    start_offset_ms: float | None = None
    duration_ms: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    child_count: int | None = None
    children: list[TraceNode] = Field(default_factory=list)
    children_truncated: bool | None = None
    io_preview: Any = None

    def walk(self, depth: int = 0):
        """Yield ``(depth, node)`` for this node and every descendant, depth-first."""
        yield depth, self
        for child in self.children:
            yield from child.walk(depth + 1)

    def find(self, *, span_type: str | None = None, name: str | None = None) -> list[TraceNode]:
        """All descendants (including self) matching ``span_type`` and/or ``name``."""
        return [
            n
            for _, n in self.walk()
            if (span_type is None or n.span_type == span_type) and (name is None or n.name == name)
        ]


class ExecutionTree(_Lenient):
    """``GET /api/ai/traces/{trace_id}/execution-tree`` -- a run plus every run it submitted.

    This is what the Trace Flow panel renders: an investigation fans out into
    child traces (one per sub-agent run), stitched into one ``root`` tree.
    ``traces`` lists the stitched trace ids.
    """

    root_trace_id: str | None = None
    traces: list[Any] = Field(default_factory=list)
    root: TraceNode | None = None

    @property
    def total_steps(self) -> int:
        """Node count -- the panel's *Total Steps*."""
        return sum(1 for _ in self.root.walk()) if self.root else 0

    def count_by_type(self) -> dict[str, int]:
        """``{span_type: count}`` over the whole tree."""
        counts: dict[str, int] = {}
        if self.root:
            for _, n in self.root.walk():
                counts[n.span_type or "?"] = counts.get(n.span_type or "?", 0) + 1
        return counts


#: Who decided a traced tool call should run -- see :class:`TracedToolCall`.
TOOL_SELECTED_BY = ("code", "llm", "llm-chained")

_NO_INFORMATION = "no information available"


class TracedToolCall(_Lenient):
    """One tool call recovered from a ``TOOL`` span, with who chose it.

    ``selected_by`` says what decided the call should run:

    * ``"code"`` -- no LLM step asked for it; the agent's own code ran it (fixed
      lookups such as ``get_indicators``/``enrich_indicator`` on every IOC).
    * ``"llm"`` -- an LLM step's output named it, but that step's prompt held no
      earlier tool results: a one-shot pick made blind.
    * ``"llm-chained"`` -- the choosing LLM step had already been given tool
      results (``role: tool`` messages): it read a result and called again.
      Only this is multi-step reasoning over tool output.

    ``server`` is the registered MCP server that ran it (``mcp_server_name``;
    ``None`` when fsr-ai refused the tool before dispatch). ``output`` is the
    tool's result, JSON-decoded when it was JSON text; ``result_chars`` is its
    raw size.
    """

    span_id: str | None = None
    trace_id: str | None = None
    tool_name: str | None = None
    args: Any = None
    server: str | None = None
    server_id: str | None = None
    status: str | None = None
    error: Any = None
    cached: bool = False
    output: Any = None
    result_chars: int = 0
    selected_by: str = "code"
    agent: str | None = None
    question: str | None = None
    start_time: str | None = None

    @property
    def failed(self) -> bool:
        """The call errored or was refused (e.g. ``"Tool not allowed : <name>"``)."""
        return bool(self.error) or (self.status or "").upper() == "ERROR"

    @property
    def llm_selected(self) -> bool:
        return self.selected_by in ("llm", "llm-chained")


class AgentRun(_Lenient):
    """One agent run (one sub-trace) of an investigation, as the Trace Flow shows it.

    A provider agent run answers one investigation question: ``question`` comes
    from its root span input, ``answer``/``evidence``/``confidence`` from its root
    span output. Planner/hypothesis/summary runs have no ``question``.
    ``steps`` is the ordered TOOL/LLM sequence, e.g.
    ``["T:get_indicators", "LLM->get_alerts_linked_to_indicators",
    "LT:get_alerts_linked_to_indicators", "LLM"]`` (``T`` = code-run tool,
    ``LT`` = LLM-chosen tool, ``LLM->x`` = an LLM step that asked for ``x``).
    ``errors`` lists every non-plumbing span that ended in ``ERROR``.
    """

    trace_id: str
    agent: str | None = None
    status: str | None = None
    question: str | None = None
    answer: Any = None
    evidence: Any = None
    confidence: Any = None
    tool_calls: list[TracedToolCall] = Field(default_factory=list)
    llm_calls: int = 0
    steps: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    #: this run's own token totals (``input_tokens``/``output_tokens``/``total_tokens``/``llm_calls``)
    tokens: dict[str, Any] = Field(default_factory=dict)

    @property
    def no_information(self) -> bool:
        """The agent answered fsr-ai's "No information available"."""
        return _NO_INFORMATION in str(self.answer or "").lower()

    @property
    def llm_picks(self) -> int:
        """Tool calls an LLM step chose (``llm`` + ``llm-chained``)."""
        return sum(1 for c in self.tool_calls if c.llm_selected)

    @property
    def chained(self) -> bool:
        """At least one tool call was chosen after reading an earlier tool result."""
        return any(c.selected_by == "llm-chained" for c in self.tool_calls)


_TOKEN_KEYS = ("input_tokens", "output_tokens", "total_tokens", "llm_calls")


def sum_tokens(parts: list[dict[str, Any]]) -> dict[str, int]:
    """Add up tracer token totals (``input_tokens``/``output_tokens``/``total_tokens``/``llm_calls``)."""
    return {key: sum(int((p or {}).get(key) or 0) for p in parts) for key in _TOKEN_KEYS}


class InvestigationTrace(_Lenient):
    """A whole alert investigation from the tracer store -- see
    :meth:`~pyfsr.api.ai.AITracesAPI.investigation`.

    ``runs`` holds the root run (``trace_id == task_id``) and every sub-agent run
    it submitted. ``tokens`` is the whole investigation's token totals
    (``input_tokens``/``output_tokens``/``total_tokens``/``llm_calls``): the
    root trace's ``tokens/{task_id}`` figures, which already include every
    sub-agent run. Each run's ``tokens`` is its own share, so they add up to it.
    """

    task_id: str
    status: str | None = None
    runs: list[AgentRun] = Field(default_factory=list)
    tokens: dict[str, Any] = Field(default_factory=dict)

    def tokens_by_agent(self) -> dict[str, dict[str, int]]:
        """Token totals per agent name, largest first."""
        out: dict[str, dict[str, int]] = {}
        for r in self.runs:
            row = out.setdefault(r.agent or "(unknown)", dict.fromkeys(_TOKEN_KEYS, 0))
            for key in _TOKEN_KEYS:
                row[key] += int(r.tokens.get(key) or 0)
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["total_tokens"]))

    @property
    def questions(self) -> list[AgentRun]:
        """The runs that answered an investigation question."""
        return [r for r in self.runs if r.question]

    @property
    def tool_calls(self) -> list[TracedToolCall]:
        """Every tool call of the investigation, run by run, in time order within a run."""
        return [c for r in self.runs for c in r.tool_calls]

    def metrics(self) -> dict[str, Any]:
        """How the agents used tools, question by question.

        8.0.0 made exactly one LLM-chosen tool call per question and never
        chained. ``questions_chained > 0`` is the evidence that an agent read a
        tool result and called again; ``questions_multi_tool`` alone mostly
        counts code-driven lookups.
        """
        qs = self.questions
        per_q = [len(r.tool_calls) for r in qs]
        picks = [r.llm_picks for r in qs]
        calls = self.tool_calls
        by: dict[str, int] = {}
        servers: dict[str, int] = {}
        for c in calls:
            by[c.selected_by] = by.get(c.selected_by, 0) + 1
            key = c.server or "(unattributed)"
            servers[key] = servers.get(key, 0) + 1
        hist: dict[int, int] = {}
        for n in per_q:
            hist[n] = hist.get(n, 0) + 1
        no_info = [r for r in qs if r.no_information]
        return {
            "agent_runs": len(self.runs),
            "questions": len(qs),
            "questions_with_tools": sum(1 for n in per_q if n),
            "questions_multi_tool": sum(1 for n in per_q if n > 1),
            "questions_multi_llm_pick": sum(1 for n in picks if n > 1),
            "questions_chained": sum(1 for r in qs if r.chained),
            "max_tools_per_question": max(per_q, default=0),
            "max_llm_picks_per_question": max(picks, default=0),
            "mean_tools_per_question": round(sum(per_q) / len(per_q), 2) if per_q else 0.0,
            "tools_per_question_hist": dict(sorted(hist.items())),
            "total_tool_calls": len(calls),
            "selected_by": by,
            "failed_tool_calls": sum(1 for c in calls if c.failed),
            "cached_tool_calls": sum(1 for c in calls if c.cached),
            "servers": dict(sorted(servers.items(), key=lambda kv: -kv[1])),
            "no_information_answers": len(no_info),
            "no_information_after_tools": sum(1 for r in no_info if r.tool_calls),
            "agent_errors": sum(len(r.errors) for r in self.runs),
            "tokens": self.tokens,
            "tokens_by_agent": self.tokens_by_agent(),
        }


# ------------------------------------------------------- chat / orchestrator (8.0.1)


class PendingInput(_Lenient):
    """The open question on a paused chat/orchestrator turn.

    ``type`` is ``"clarification"`` (the planner needs a value -- reply with it)
    or ``"approval"`` (the executor wants a yes/no before a state-changing
    action -- reply approve/deny). ``expected_type`` is ``entity``, ``choice`` or
    ``decision``; ``options`` are the renderable choices.
    """

    pause_id: str | None = None
    type: str | None = None
    question: str | None = None
    expected_type: str | None = None
    options: list[dict[str, Any]] = Field(default_factory=list)
    expires_at: str | None = None
    round: int | None = None


class AgentTurn(_Lenient):
    """One turn of an :class:`~pyfsr.api.ai.AgentSession` (chat or orchestrator).

    ``status`` is the agent's own outcome when it reports one (orchestrator:
    ``success``, ``success_no_action``, ``partial``, ``failed``, ``denied``,
    ``awaiting_approval``, ``needs_clarification``, ``rejected``,
    ``unplannable``), else the task status (``completed`` / ``failed`` / ... or
    a non-terminal ``pending``/``inprogress`` if the wait timed out).
    """

    task_id: str | None = None
    status: str | None = None
    answer: str | None = None
    pending: PendingInput | None = None
    request_id: str | None = None
    response_id: str | None = None
    error: Any = None
    phases: Any = None
    logs: Any = None

    @property
    def needs_clarification(self) -> bool:
        pending_type = self.pending.type if self.pending is not None else None
        return self.status == "needs_clarification" or pending_type == "clarification"

    @property
    def awaiting_approval(self) -> bool:
        pending_type = self.pending.type if self.pending is not None else None
        return self.status == "awaiting_approval" or pending_type == "approval"

    @property
    def is_paused(self) -> bool:
        """The turn stopped for a reply (clarification or approval)."""
        return self.needs_clarification or self.awaiting_approval


TraceNode.model_rebuild()
