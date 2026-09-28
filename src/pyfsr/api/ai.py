"""FortiAI -- agentic alert investigation, LLM-provider and MCP-server management.

FortiSOAR 8.0 ships an on-appliance agentic AI service (``fsr-ai``) mounted at
``/api/ai``. This module wraps the three things you actually drive from a
client:

- **Investigation** -- fire the multi-agent triage pipeline at an alert
  (normalize → hypothesize → plan → gather evidence over MCP → verdict) and poll
  for the result. See :meth:`AIApi.investigate_alert`.
- **LLM providers** -- list the configured reasoning profiles and the
  provider/model catalogue (``/api/ai/llm``).
- **MCP servers** -- list, validate and register the Model Context Protocol
  servers the investigation agents are allowed to call (``/api/ai/mcp`` +
  the ``mcp_configurations`` collection).

Plus the one-time enablement gate: FortiAI features must be turned on (and the
AI terms accepted) in System Settings before any of this works -- see
:meth:`AIApi.enable_features`, which writes ``publicValues.ai_feature``.

Accessed as ``client.ai``.

Example:
    >>> client = FortiSOAR("soar.example.com", token=api_key)
    >>> client.ai.enable_features()                       # one-time, accepts AI T&C
    >>> report = client.ai.investigate_alert("alerts:740a751c-...", wait=True)
    >>> report["status"]
    'completed'

Endpoint reference (verified against FSR 8.0 ``fsr-ai``):

================================================  ==================================================
Operation                                         Endpoint
================================================  ==================================================
start investigation                               ``POST /api/ai/triage/alert``
find an alert's current investigation             read ``alert["triagetaskid"]``
poll status                                       ``GET  /api/ai/agents/{task_id}/status``
fetch result/verdict                              ``GET  /api/ai/agents/{task_id}/result``
run one agent                                     ``POST /api/ai/agents/{agent_name}/trigger``
read one agent's input contract                   ``GET  /api/ai/agent/{name}/{version}`` (``inputformat``)
submit verdict feedback                           ``POST /api/ai/agents/{task_id}/acceptance``
list reasoning profiles                           ``GET  /api/ai/llm/config``
list providers                                    ``GET  /api/ai/llm/allowed-providers``
list MCP servers                                  ``GET  /api/ai/mcp``
MCP server health                                 ``GET  /api/ai/mcp/status``
validate an MCP server config                     ``POST /api/ai/mcp/validate``
register an MCP server                            ``POST /api/3/mcp_configurations``
update a registered MCP server                    ``PUT  /api/3/mcp_configurations/{uuid}``
list AI agents                                    ``GET  /api/ai/agent/``
get one AI agent                                  ``GET  /api/ai/agent/{name}/{version}``
install an agent package (zip)                     ``POST /ai/agent/import``
export an installed agent as a zip                 ``POST /api/ai/agent/export/{agent_id}``
get an agent's configuration                      ``GET  /api/ai/agent/config/{name}/{version}``
update an agent's configuration                   ``POST /api/ai/agent/config``
get/update the default agent configuration        ``GET/POST /api/ai/agent/config/default``
activate/deactivate agents                        ``POST /api/ai/agent/activate``
uninstall an agent                                ``DELETE /api/ai/agent/{name}/{version}``
AI Insights (plan, execute, save, trigger)        ``/api/ai/insight/*`` (``client.ai.insights``)
read/edit the planner's tool table                ``/api/3/organizational_contexts`` (``INFRA_INFO/TOOL_LIST``)
which connectors can be hosted as an MCP server    ``GET  /mcp/servers/connector``
host a connector as an MCP server                  ``POST /mcp/add/tools`` (+ ``mcp_configurations``)
change a hosted connector server's exposed tools   ``PUT  /mcp/tools/{uuid}``
list a hosted server's current tools               ``POST /mcp/config/export``
remove specific tools from a hosted server         ``DELETE /mcp/tools/delete``
================================================  ==================================================

.. note::

    **Reachability:** The AI service publishes 55 operations in its OpenAPI,
    but only ~41 are reachable through the PHP proxy's permission map. A
    request matching no group gets a bare ``403 Access Denied`` --
    indistinguishable from a missing-role 403. Before wiring a new
    ``/api/ai/*`` endpoint, check the permission map in ``parameters.yaml``
    (``app_proxy.handlers.ai``). Dead methods removed: ``list_models()``
    (``/api/ai/llm/models`` -- unauthorised), ``test_llm_config()``
    (``/api/ai/llm/test`` -- not in the service spec). Replaced by
    :meth:`AIApi.verify_llm_config` (``GET /api/ai/llm/config/{uuid}/verify``).

Connector → MCP server
-----------------------
Any installed connector can be *hosted* as an MCP server -- each operation
becomes a tool -- via :meth:`AIApi.host_connector_as_mcp_server`. This is
distinct from :meth:`AIApi.register_mcp_server` (connecting to an *external*
MCP server); both land in the same ``mcp_configurations`` collection,
distinguished by ``type`` (``"connector"`` vs ``"internal"``/``"external"``).
Check :meth:`AIApi.mcp_connector_candidates` first -- some connectors can't be
hosted this way. These ``/mcp/...`` routes (unlike everything else here) live
at the appliance root rather than under ``/api/3``.

Agent ↔ MCP binding
-------------------
Which MCP servers a triage agent may call is stored on the agent's
configuration as ``config["mcp_server"]`` -- a list of registered MCP-server
UUIDs. To let an agent reach a newly-registered server (e.g. FortiSIEM), append
its uuid to that list and ``PUT`` the config back; the high-level
:meth:`AIApi.allow_mcp_server_for_agent` does the read-modify-write for you.
"""

from __future__ import annotations

import json
import re
import time
import uuid as _uuidlib
import warnings
import zipfile
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..exceptions import APIError, ResourceNotFoundError
from ..exceptions import PermissionError as FortiSOARPermissionError
from ..models._ai import (
    AgentConfig,
    AgentConfigDTO,
    AgentRecord,
    AgentRun,
    AgentRunResult,
    AgentTurn,
    ConnectorMcpCandidates,
    ExecutionTree,
    FortiAITokenBalance,
    InsightExecution,
    InsightPlan,
    InsightRecord,
    InvestigationHandle,
    InvestigationQuestion,
    InvestigationResult,
    InvestigationTool,
    InvestigationTrace,
    LLMConfig,
    LLMProvider,
    MCPServerConfig,
    MCPServerRef,
    MCPServerStatus,
    MCPTool,
    MCPToolResult,
    MCPValidateResult,
    ToolCall,
    TracedToolCall,
    TraceNode,
    TraceSpan,
    TraceSummary,
    sum_tokens,
)
from ..models._ai_agent_package import AgentPackage
from ..pagination import extract_members
from ..utils.iri import uuid_from_iri
from ..utils.validation import is_uuid as _is_uuid
from .base import BaseAPI

#: Triage/agent statuses that mean the pipeline has stopped running. While running,
#: the pipeline reports ``"pending"`` then ``"inprogress"``; it ends on one of these.
TERMINAL_STATUSES = frozenset({"completed", "failed", "error", "cancelled"})

#: Key on an agent's ``config`` dict holding the list of allowed MCP-server UUIDs.
AGENT_CONFIG_MCP_KEY = "mcp_server"

#: Alert field where FortiSOAR stores the task_id of the alert's current
#: investigation. The UI writes it after starting triage; reading it is the
#: direct alert→investigation link. Ships with the AI solution pack, so it may be
#: absent on appliances without FortiAI installed (treat a missing value as None).
ALERT_TRIAGE_TASK_KEY = "triagetaskid"


def pack_agent(source_dir: str, output: str | None = None, *, validate: bool = True) -> str:
    """Bundle an AI agent source folder into a FortiSOAR-importable ``.zip``.

    ``source_dir`` is the package root -- the folder that *is* the agent (holds
    ``info.json``, ``agent.py``, ``prompt.yaml``, ``config/memory.yaml``,
    ``images/``). The archive's single top-level folder is the agent's
    ``info.json`` ``name`` (``<name>/info.json`` …), whatever the source folder
    is called: fsr-ai's importer unpacks to ``temp/<name>`` and fails with
    ``No such file or directory`` otherwise. It is the layout FortiSOAR's own
    agent export produces.

    With ``validate=True`` (default) the package is parsed and consistency-checked
    (:meth:`~pyfsr.models.AgentPackage.validate_consistency`) before packing, so an
    ``agentclass`` that doesn't exist in ``agent.py`` or a prompt uuid the code
    references but ``prompt.yaml`` omits fails *here*, not silently on the box.

    Returns the path to the written ``.zip`` (defaults to ``<source_dir>.zip``
    beside the source folder).

    Compiled artifacts (``__pycache__``, ``*.pyc``) and VCS/OS cruft are excluded.
    """
    root = Path(source_dir).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"agent source dir not found: {source_dir}")
    if validate:
        AgentPackage.from_dir(str(root))  # raises on a bad manifest/consistency

    try:
        top = json.loads((root / "info.json").read_text(encoding="utf-8")).get("name") or root.name
    except (OSError, ValueError):
        top = root.name
    out_path = Path(output) if output else root.with_suffix(".zip")
    _excluded = {"__pycache__", ".git", ".DS_Store", ".idea", ".vscode"}
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            if any(part in _excluded for part in path.relative_to(root).parts):
                continue
            if path.suffix == ".pyc":
                continue
            zf.write(path, arcname=str(Path(top) / path.relative_to(root)))
    return str(out_path)


#: Trace/tree I/O detail levels accepted by the tracer endpoints.
TRACE_IO_MODES = frozenset({"full", "summary", "none"})

_TRACES = "/api/ai/traces"

#: Root trace name of an alert investigation in the tracer store (8.0.1).
INVESTIGATION_TRACE_NAME = "Alert Investigation"
#: Name the UI gives an agent config forked from the default one.
CUSTOM_AGENT_CONFIG_NAME = "Custom Configuration"


class LLMSetupError(RuntimeError):
    """A third-party LLM connector could not be set up or does not answer
    (see :meth:`AIApi.setup_connector_llm`)."""


#: the solution pack that fronts FortiAI (Fortinet-hosted LLMs, metered in FortiAI tokens)
FORTIAI_PROXY_CONNECTOR = "fortinet-fortiai-proxy"


_INSIGHT = "/api/ai/insight"
#: ``plan/{task_id}/status`` values that end polling.
_INSIGHT_DONE = frozenset({"completed", "failed"})


class AIInsightsAPI(BaseAPI):
    """AI Insights (8.0.1): a question about your data, planned and answered by agents.

    The Insight cards widget's flow, step by step: :meth:`chain_of_thought`
    (LLM), :meth:`plan` (LLM), :meth:`execute` (agents run the plan's steps,
    then the summary agent writes the insight), then :meth:`save` to keep it as
    an ``insights`` record. :meth:`create` does all four. The shipped
    ``insight_templates`` carry ready plans: :meth:`run_template` executes one
    without the two planning calls.

    ``socrole`` is one of the ``SOC Role`` picklist values (``SOC Analyst``,
    ``SOC Manager``, ``Threat Analyst``, ``Infrastructure Admin``); it shapes
    the plan and the summary.

    Accessed as ``client.ai.insights``.

    Example:
        >>> ins = client.ai.insights.create("Critical this week", query="Which critical alerts ...")  # doctest: +SKIP
        >>> ins.result["concise_summary"]  # doctest: +SKIP
    """

    def chain_of_thought(self, query: str, *, socrole: str = "SOC Analyst") -> list[str]:
        """Reasoning steps for ``query`` (``POST /api/ai/insight/chain_of_thoughts``, one LLM call)."""
        resp = self.client.post(f"{_INSIGHT}/chain_of_thoughts", data={"query": query, "socrole": socrole})
        return list((resp or {}).get("chain_of_thought") or [])

    def plan(
        self, query: str, *, chain_of_thought: list[str] | None = None, socrole: str = "SOC Analyst"
    ) -> InsightPlan:
        """Plan ``query`` into agent steps (``POST /api/ai/insight/plan``, one LLM call).

        Generates the chain of thought first when none is given. Check
        ``plan.feasible`` before executing; the UI refuses an infeasible plan.
        """
        if chain_of_thought is None:
            chain_of_thought = self.chain_of_thought(query, socrole=socrole)
        resp = self.client.post(
            f"{_INSIGHT}/plan", data={"query": query, "chain_of_thought": chain_of_thought, "socrole": socrole}
        )
        plan = InsightPlan.model_validate(resp if isinstance(resp, dict) else {})
        if not plan.chain_of_thought:
            plan.chain_of_thought = list(chain_of_thought)
        return plan

    def execute(
        self,
        plan: InsightPlan | dict[str, Any],
        *,
        socrole: str = "SOC Analyst",
        wait: bool = True,
        interval: float = 5.0,
        timeout: float = 900.0,
    ) -> InsightExecution:
        """Run a plan (``POST /api/ai/insight/plan/execute``) and, by default, wait for the insight.

        Each run stores the plan and returns a new ``planid``, which is what
        :meth:`save` records. On timeout the execution comes back with its
        non-terminal status instead of raising.
        """
        body = plan.model_dump() if isinstance(plan, InsightPlan) else dict(plan)
        resp = self.client.post(f"{_INSIGHT}/plan/execute", data={"plan": body, "socrole": socrole}) or {}
        run = InsightExecution(task_id=resp.get("task_id"), planid=resp.get("planid"), status=resp.get("status"))
        if not wait or not run.task_id:
            return run
        deadline = time.monotonic() + timeout
        status = self.status(run.task_id)
        while status not in _INSIGHT_DONE and time.monotonic() < deadline:
            time.sleep(interval)
            status = self.status(run.task_id)
        run.status = status
        if status in _INSIGHT_DONE:
            result = self.result(run.task_id)
            run.result, run.execution_log, run.last_executed = result.result, result.execution_log, result.last_executed
        return run

    def status(self, task_id: str) -> str | None:
        """Status of a plan execution (``GET /api/ai/insight/plan/{task_id}/status``)."""
        return (self.client.get(f"{_INSIGHT}/plan/{task_id}/status") or {}).get("status")

    def result(self, task_id: str) -> InsightExecution:
        """Result of a plan execution (``GET /api/ai/insight/plan/{task_id}/result``)."""
        resp = self.client.get(f"{_INSIGHT}/plan/{task_id}/result")
        run = InsightExecution.model_validate(resp if isinstance(resp, dict) else {})
        run.task_id = run.task_id or task_id
        return run

    def save(
        self, title: str, *, query: str, planid: str, socrole: str = "SOC Analyst", insight_id: str | None = None
    ) -> str:
        """Save (or update) an insight as an ``insights`` record; returns its uuid.

        Mirrors the widget: ``POST /api/3/insights`` (``PUT`` with
        ``insight_id``) with ``{title, query, socrole, planid}``. ``planid`` comes
        from :meth:`execute`. The widget also creates a schedule for it, which is
        not done here; :meth:`trigger` runs a saved insight on demand.
        """
        body = {"title": title, "query": query, "socrole": socrole, "planid": planid}
        if insight_id:
            resp = self.client.put(f"/api/3/insights/{insight_id}", data=body)
        else:
            resp = self.client.post("/api/3/insights", data=body)
        return str((resp or {}).get("uuid") or insight_id or "")

    def create(
        self,
        title: str,
        *,
        query: str,
        socrole: str = "SOC Analyst",
        save: bool = True,
        interval: float = 5.0,
        timeout: float = 900.0,
    ) -> InsightExecution:
        """Plan, execute and (by default) save a new insight: the widget's *Add Insight* flow.

        Raises :class:`ValueError` when the plan is infeasible (the reason is in
        the message). The returned execution carries the saved record's uuid as
        ``insight_id`` when ``save=True``. Costs two planning LLM calls plus the
        agents the plan runs.
        """
        plan = self.plan(query, socrole=socrole)
        if not plan.feasible:
            raise ValueError(f"insight plan is infeasible: {(plan.feasibility or {}).get('reason')}")
        run = self.execute(plan, socrole=socrole, interval=interval, timeout=timeout)
        if save and run.status == "completed" and run.planid:
            run.insight_id = self.save(title, query=query, planid=run.planid, socrole=socrole)
        return run

    def list(self) -> list[InsightRecord]:
        """The current user's active insights (``GET /api/ai/insight/``)."""
        return [InsightRecord.model_validate(r) for r in _as_list(self.client.get(f"{_INSIGHT}/"))]

    def get(self, insight_id: str) -> InsightRecord:
        """One insight with its plan and last result (``GET /api/ai/insight/{id}``)."""
        resp = self.client.get(f"{_INSIGHT}/{insight_id}")
        return InsightRecord.model_validate(resp if isinstance(resp, dict) else {})

    def delete(self, insight_id: str) -> None:
        """Delete an insight (``DELETE /api/ai/insight/{id}``)."""
        self.client.delete(f"{_INSIGHT}/{insight_id}")

    def trigger(self, insight_id: str, *, user_id: str | None = None) -> str:
        """Re-run a saved, active insight now (``POST /api/ai/insight/trigger/schedule``).

        The endpoint the insight's schedule calls. ``user_id`` is the owner's
        person uuid (the widget sends the creator's); it defaults to the saved
        record's ``createUser``. Returns fsr-ai's message.
        """
        if user_id is None:
            record = self.client.get(f"/api/3/insights/{insight_id}") or {}
            owner = record.get("createUser")
            user_id = owner.get("@id") if isinstance(owner, dict) else owner
            user_id = uuid_from_iri(user_id) if user_id else None
        if not user_id:
            raise ValueError("cannot resolve the insight's owner; pass user_id=")
        resp = self.client.post(f"{_INSIGHT}/trigger/schedule", data={"referenceid": insight_id, "createUser": user_id})
        return str((resp or {}).get("message", ""))

    def templates(self) -> list[Any]:
        """The ``insight_templates`` records: ready-made questions with plans."""
        from ..query import Query

        return list(self.client.records("insight_templates").iterate(Query().limit(100)))

    def run_template(self, template: Any, **kwargs: Any) -> InsightExecution:
        """Execute an insight template's stored plan with its ``socrole`` (no planning LLM calls).

        ``template`` is a record from :meth:`templates` or its title.
        """
        if isinstance(template, str):
            match = [t for t in self.templates() if t.get("title") == template]
            if not match:
                raise ValueError(f"no insight template titled {template!r}")
            template = match[0]
        return self.execute(template.get("plan") or {}, socrole=template.get("socrole") or "SOC Analyst", **kwargs)


class AITracesAPI(BaseAPI):
    """Agent traceability (8.0.1) -- the tracer store behind the Trace Flow panel.

    Every agent run (investigation, chat turn, orchestrator request, single-agent
    trigger) records a trace: a tree of spans, one per agent step, LLM call, tool
    call, skill lookup or org-context search. A run's trace id is its task id.

    Accessed as ``client.ai.traces``.

    Example:
        >>> tree = client.ai.traces.execution_tree(task_id)  # doctest: +SKIP
        >>> tree.total_steps, tree.count_by_type()  # doctest: +SKIP
        (173, {'AGENT': 57, 'LLM': 29, ...})
        >>> for depth, node in tree.root.walk():  # doctest: +SKIP
        ...     print("  " * depth, node.name, node.duration_ms)
    """

    def health(self) -> bool:
        """``True`` when the tracer store answers ``GET /api/ai/traces/health``."""
        resp = self.client.get(f"{_TRACES}/health")
        return isinstance(resp, dict) and resp.get("status") == "ok"

    def list(
        self,
        *,
        status: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
        cursor: str | float | None = None,
        project_id: str = "default",
    ) -> list[TraceSummary]:
        """One page of top-level traces, newest first.

        Args:
            status: filter on trace status (``"OK"``, ``"ERROR"``, ``"RUNNING"``).
            session_id: filter to one chat session.
            limit: page size, 1-500.
            cursor: return traces that *started before* this point -- an epoch
                float (what the server compares against). Use :meth:`iter` rather
                than paging by hand; it derives the cursor from each page.
                **Broken server-side on 8.0.1** -- see :meth:`iter`.
            project_id: tracer project (always ``"default"`` on the appliance).
        """
        if not 1 <= limit <= 500:
            raise ValueError(f"limit must be 1-500, got {limit}")
        params: dict[str, Any] = {"project_id": project_id, "limit": limit}
        if status:
            params["status"] = status
        if session_id:
            params["session_id"] = session_id
        if cursor is not None:
            params["cursor"] = str(cursor)
        resp = self.client.get(f"{_TRACES}/", params=params)
        rows = resp.get("traces", []) if isinstance(resp, dict) else []
        return [TraceSummary.model_validate(r) for r in rows]

    def iter(
        self,
        *,
        status: str | None = None,
        session_id: str | None = None,
        page_size: int = 100,
        max_items: int | None = None,
    ) -> Iterator[TraceSummary]:
        """Yield every matching trace, newest first, paging with the start-time cursor.

        The server pages on ``start_time < cursor`` (an epoch float) but returns
        ``start_time`` as ISO text, so the cursor is converted from the last row
        of each page.

        .. warning::
           On fsr-ai 8.0.1 any ``cursor`` fails server-side (``operator does not
           exist: timestamp with time zone < numeric`` -- the service insists on
           a float, then compares it to a ``timestamptz`` column). When that
           happens this stops after the first page with a :class:`UserWarning`
           rather than raising, so only the newest ``page_size`` traces (max 500)
           are reachable on that build. Use ``page_size=500`` to see the most.
        """
        cursor: float | None = None
        yielded = 0
        while True:
            try:
                page = self.list(status=status, session_id=session_id, limit=page_size, cursor=cursor)
            except APIError as err:
                if cursor is None or "timestamp with time zone < numeric" not in str(err):
                    raise
                warnings.warn(
                    "fsr-ai rejects the trace-list cursor on this build (timestamptz < numeric); "
                    f"stopping after the first {yielded} trace(s)",
                    UserWarning,
                    stacklevel=2,
                )
                return
            for trace in page:
                yield trace
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return
            if len(page) < page_size or not page[-1].start_time:
                return
            cursor = datetime.fromisoformat(page[-1].start_time).timestamp()

    def get(self, trace_id: str, *, depth: int = 3, io: str = "summary") -> TraceNode:
        """One trace as a span tree (``GET /api/ai/traces/{trace_id}``).

        Only this trace's own spans; use :meth:`execution_tree` to follow the
        sub-agent runs it submitted.
        """
        _check_io(io)
        resp = self.client.get(f"{_TRACES}/{trace_id}", params={"depth": depth, "io": io})
        return TraceNode.model_validate((resp or {}).get("root") or {})

    def execution_tree(
        self, trace_id: str, *, depth: int = 10, io: str = "summary", max_traces: int = 25
    ) -> ExecutionTree:
        """The full run tree the Trace Flow panel renders (``.../{trace_id}/execution-tree``).

        Stitches in every trace this run submitted (one per sub-agent run), up to
        ``max_traces`` (1-100). An investigation typically spans ~10 traces.
        """
        _check_io(io)
        resp = self.client.get(
            f"{_TRACES}/{trace_id}/execution-tree", params={"depth": depth, "io": io, "max_traces": max_traces}
        )
        return ExecutionTree.model_validate(resp if isinstance(resp, dict) else {})

    def spans(self, trace_id: str) -> list[TraceSpan]:
        """Every span of one trace, flat, with full I/O (``.../{trace_id}/spans``)."""
        resp = self.client.get(f"{_TRACES}/{trace_id}/spans")
        rows = resp.get("spans", []) if isinstance(resp, dict) else []
        return [TraceSpan.model_validate(r) for r in rows]

    def span(self, span_id: str) -> TraceSpan:
        """One span with full input/output -- the Step Details panel (``.../spans/{span_id}``)."""
        return TraceSpan.model_validate(self.client.get(f"{_TRACES}/spans/{span_id}") or {})

    def span_subtree(self, span_id: str, *, depth: int = 10, io: str = "summary") -> TraceNode:
        """The subtree rooted at one span, e.g. a single agent's work inside a larger run.

        .. note::
           On fsr-ai 8.0.1 ``GET .../spans/{span_id}/tree`` returns 404 for every
           span except a trace's root: it collects the subtree, then looks for a
           row with no parent to use as the root, and a non-root span always has
           one. On that 404 this falls back to fetching the span's own trace tree
           and returning the matching node (``depth`` then counts from the
           trace root, capped at 50).
        """
        _check_io(io)
        try:
            resp = self.client.get(f"{_TRACES}/spans/{span_id}/tree", params={"depth": depth, "io": io})
            return TraceNode.model_validate((resp or {}).get("root") or {})
        except ResourceNotFoundError:
            trace_id = self.span(span_id).trace_id
            if not trace_id:
                raise
            root = self.get(trace_id, depth=50, io=io)
            match = next((n for _, n in root.walk() if n.span_id == span_id), None)
            if match is None:
                raise
            return match

    def span_children(self, span_id: str) -> list[dict[str, Any]]:
        """Direct children of a span (``.../spans/{span_id}/children``)."""
        resp = self.client.get(f"{_TRACES}/spans/{span_id}/children")
        return resp.get("children", []) if isinstance(resp, dict) else []

    def span_lineage(self, span_id: str, *, direction: str = "both", max_hops: int = 5) -> dict[str, Any]:
        """Data lineage around a span: which spans fed it / consumed its output.

        .. note::
           Always fails on fsr-ai 8.0.1 (HTTP 500 ``TracerStoreService has no
           attribute 'get_lineage'`` -- the route calls a service method that
           does not exist). Kept for builds that implement it.

        Args:
            direction: ``"both"``, ``"upstream"`` or ``"downstream"``.
            max_hops: 1-25.
        """
        if direction not in ("both", "upstream", "downstream"):
            raise ValueError(f"direction must be both/upstream/downstream, got {direction!r}")
        return self.client.get(
            f"{_TRACES}/spans/{span_id}/lineage", params={"direction": direction, "max_hops": max_hops}
        )

    def tokens(self, trace_id: str) -> dict[str, Any]:
        """Token totals for one trace (``.../tokens/{trace_id}``)."""
        return self.client.get(f"{_TRACES}/tokens/{trace_id}")

    def session_tokens(self, session_id: str) -> dict[str, Any]:
        """Token totals across a chat session (``.../tokens/session/{session_id}``)."""
        return self.client.get(f"{_TRACES}/tokens/session/{session_id}")

    def llm_calls(self, trace_id: str) -> list[TraceSpan]:
        """Every LLM call in a run and its sub-runs, with provider/model/usage filled in.

        Walks :meth:`execution_tree` and fetches each ``LLM`` span's detail -- one
        request per call, so expect ~30 requests for an alert investigation.
        """
        tree = self.execution_tree(trace_id)
        if not tree.root:
            return []
        return [self.span(n.span_id) for n in tree.root.find(span_type="LLM")]

    def agent_run(self, trace_id: str) -> AgentRun:
        """One agent run (one trace) with its question, answer and labelled tool calls.

        Fetches the trace's spans once. Each ``TOOL`` span becomes a
        :class:`~pyfsr.models.TracedToolCall` whose ``selected_by`` says whether
        agent code ran it, an LLM step picked it blind, or an LLM step picked it
        after reading earlier tool results (``llm-chained``). Calls are matched to
        the LLM step that named them by tool name, in time order -- the LLM span's
        arguments are masked (``SM_..._EM`` tokens), so they can't be compared.
        """
        return _agent_run(trace_id, self.spans(trace_id))

    def investigation(self, task_id: str, *, max_traces: int = 100) -> InvestigationTrace:
        """An alert investigation rebuilt from its traces (8.0.1).

        The investigation's ``task_id`` is its root trace id. This fetches the
        execution tree (to list every sub-agent trace), then each trace's spans:
        roughly one request per question, ~20 for a typical investigation. Use
        :meth:`~pyfsr.models.InvestigationTrace.metrics` for the per-question
        tool-use summary. One ``tokens`` request per run fills in each run's
        token totals (the root run keeps only its own share: the root trace's
        totals already include every sub-run, and are ``InvestigationTrace.tokens``).

        This is the 8.0.1 replacement for reading ``llm_activity_logs``, which
        8.0.1 no longer writes for investigations.
        """
        tree = self.execution_tree(task_id, depth=1, max_traces=max_traces)
        ids = [task_id] + [t for t in tree.traces if isinstance(t, str) and t != task_id]
        runs = [self.agent_run(t) for t in ids]
        for run in runs:
            try:
                tokens = self.tokens(run.trace_id)
            except APIError:
                tokens = {}
            run.tokens = tokens if isinstance(tokens, dict) else {}
        total = runs[0].tokens if runs else {}
        if runs and total:
            # the root trace's totals already cover every sub-run; keep only its own share
            subs = sum_tokens([r.tokens for r in runs[1:]])
            runs[0].tokens = {k: max(int(total.get(k) or 0) - subs[k], 0) for k in subs}
        return InvestigationTrace(
            task_id=task_id,
            status=runs[0].status if runs else None,
            runs=runs,
            tokens=total,
        )

    def purge_older_than(self, *, days: int | None = None, before: str | None = None) -> dict[str, Any]:
        """Delete **every** trace older than a cutoff (``DELETE /api/ai/traces/delete``).

        There is no per-trace delete: the endpoint queues a background cleanup of
        all traces older than ``days`` days, or older than the ISO date/time
        ``before``. Pass exactly one. Returns ``{"task_id", "success"}``; the
        purge itself runs asynchronously.
        """
        if (days is None) == (before is None):
            raise ValueError("pass exactly one of days= or before=")
        body: dict[str, Any] = {"days": int(days)} if days is not None else {"date": before}
        return self.client.delete(f"{_TRACES}/delete", data=body)


#: Wrapper spans around every LLM call; not reasoning steps, and their errors are noise.
_PLUMBING_SPANS = frozenset({"Data Masking", "Data Unmasking"})
_TOOL_ROLE = re.compile(r'\\*"role\\*":\s*\\*"tool\\*"')
_QUESTION = re.compile(r'"question":\s*"((?:[^"\\]|\\.)*)"')


def _maybe_json(value: Any) -> Any:
    """Decode a JSON object/array held as text; anything else comes back unchanged."""
    if isinstance(value, str) and value.strip()[:1] in ("{", "["):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _question_from_input(root_input: Any) -> str | None:
    """The question a provider agent was asked.

    fsr-ai stores the agent input as ``{"input_data": {"preview": "<json text>"}}``
    and cuts long previews short, so fall back to pulling ``"question"`` out of
    the raw text when it no longer parses.
    """
    data = root_input.get("input_data") if isinstance(root_input, dict) else None
    if isinstance(data, dict):
        data = data.get("preview", data)
    obj = _maybe_json(data)
    if isinstance(obj, dict):
        return obj.get("question") or None
    m = _QUESTION.search(data if isinstance(data, str) else json.dumps(root_input, default=str))
    if not m:
        return None
    try:
        return json.loads(f'"{m.group(1)}"')
    except ValueError:
        return m.group(1)


def _llm_tool_choices(span: TraceSpan) -> list[str]:
    """Tool names an LLM span asked for (``output.tools`` / ``output.tool_name``)."""
    out = span.output if isinstance(span.output, dict) else {}
    names = [t.get("name") for t in (out.get("tools") or []) if isinstance(t, dict) and t.get("name")]
    if not names and out.get("tool_name"):
        names = [out["tool_name"]]
    return names


def _tool_span_name(span: TraceSpan) -> str | None:
    out = span.output if isinstance(span.output, dict) else {}
    return out.get("name") or span.tool_call.get("name")


def _agent_run(trace_id: str, spans: list[TraceSpan]) -> AgentRun:
    """Build an :class:`AgentRun` from one trace's flat span list."""
    roots = [s for s in spans if not s.parent_span_id]
    root = roots[0] if roots else None
    agent = root.name if root else None
    question = _question_from_input(root.input) if root else None
    result = _maybe_json(root.output) if root else None
    result = result if isinstance(result, dict) else {}

    seq = sorted(
        (s for s in spans if s.span_type in ("TOOL", "LLM") and (root is None or s.span_id != root.span_id)),
        key=lambda s: s.start_time or "",
    )
    pending: list[tuple[str, str]] = []  # (tool name, label) an LLM asked for, not yet run
    calls: list[TracedToolCall] = []
    steps: list[str] = []
    llm_calls = 0
    for s in seq:
        if s.span_type == "LLM":
            llm_calls += 1
            chained = bool(_TOOL_ROLE.search(json.dumps(s.input, default=str)))
            choices = _llm_tool_choices(s)
            pending += [(n, "llm-chained" if chained else "llm") for n in choices]
            steps.append(f"LLM->{','.join(choices)}" if choices else "LLM")
            continue
        name = _tool_span_name(s)
        label = "code"
        for i, (want, lab) in enumerate(pending):
            if want == name:
                label = lab
                del pending[i]
                break
        tr = s.tool_result
        out = s.output if isinstance(s.output, dict) else {}
        raw = tr.result if tr else out.get("result")
        calls.append(
            TracedToolCall(
                span_id=s.span_id,
                trace_id=trace_id,
                tool_name=name,
                args=_maybe_json((tr.args if tr else None) or s.tool_call.get("args")),
                server=tr.mcp_server_name if tr else None,
                server_id=tr.mcp_server_id if tr else None,
                status=s.status,
                error=(tr.error if tr else None) or s.error,
                cached=bool(tr.cached) if tr else False,
                output=_maybe_json(raw),
                result_chars=len(raw) if isinstance(raw, str) else len(json.dumps(raw, default=str)) if raw else 0,
                selected_by=label,
                agent=agent,
                question=question,
                start_time=s.start_time,
            )
        )
        steps.append(f"{'T' if label == 'code' else 'LT'}:{name}")

    errors = [
        {"span": s.name, "span_type": s.span_type, "error": s.error}
        for s in spans
        if (s.status or "").upper() == "ERROR" and s.name not in _PLUMBING_SPANS
    ]
    return AgentRun(
        trace_id=trace_id,
        agent=agent,
        status=root.status if root else None,
        question=question,
        answer=result.get("answer"),
        evidence=result.get("evidence"),
        confidence=result.get("confidence"),
        tool_calls=calls,
        llm_calls=llm_calls,
        steps=steps,
        errors=errors,
    )


def _check_io(io: str) -> None:
    if io not in TRACE_IO_MODES:
        raise ValueError(f"io must be one of {sorted(TRACE_IO_MODES)}, got {io!r}")


#: The planner's built-in tool table (8.0.1 ``investigation-planning``
#: ``get_tool_list``), used whenever no ``INFRA_INFO/TOOL_LIST`` Org Context record exists.
DEFAULT_INVESTIGATION_TOOLS: tuple[InvestigationTool, ...] = (
    InvestigationTool(avenue="Approved activity", source="Org Context", required_field="IOCs + activity"),
    InvestigationTool(avenue="Change authorization", source="ITSM", required_field="host + window"),
    InvestigationTool(avenue="Detection quality", source="SIEM (raw event)", required_field="case_id + rule"),
    InvestigationTool(avenue="Historical baseline", source="SIEM", required_field="entity"),
    InvestigationTool(avenue="Asset criticality", source="CMDB", required_field="host"),
    InvestigationTool(avenue="Identity status", source="IAM", required_field="user"),
    InvestigationTool(avenue="IOC reputation", source="Threat Intel", required_field="each public IOC"),
    InvestigationTool(avenue="Prior disposition", source="Alert Correlation", required_field="each entity"),
    InvestigationTool(avenue="Endpoint behavior", source="EDR", required_field="process"),
)

_ORG_CONTEXTS = "organizational_contexts"
_TOOL_LIST_TITLE = "Infrastructure - Investigation Tool List"

#: Task statuses from ``GET /api/ai/agents/{task_id}/status`` that end polling.
_TURN_DONE = frozenset({"completed", "failed", "error", "cancelled", "success", "awaiting_approval"})


class AgentSession:
    """A multi-turn conversation with a chat agent (8.0.1), driven like the aiAssistant widget.

    Created by :meth:`AIApi.chat` (the Conversation Agent / SOC chat) or
    :meth:`AIApi.orchestrate` (the Orchestrator Agent). Each :meth:`ask` is one
    turn: ``POST /api/ai/agents/{agent}/trigger`` with the session header
    ``X-CHAT-SESSION-ID``, then poll the task to a result. The session carries
    ``previous_response_id`` / ``request_id`` between turns, which is how the
    agent continues the same conversation or resumes a paused request.

    A paused turn (``turn.is_paused``) waits for a reply: answer a
    clarification with :meth:`reply` and an approval with :meth:`approve` /
    :meth:`deny`. Replies go to the same ``request_id``, which is what resumes
    the paused plan rather than starting a new one.

    Example:
        >>> s = client.ai.orchestrate()  # doctest: +SKIP
        >>> turn = s.ask("Block the malicious IP address on the firewall.")  # doctest: +SKIP
        >>> turn.needs_clarification, turn.pending.question  # doctest: +SKIP
        (True, 'Cannot block ... Please specify the IP address to proceed.')
        >>> turn = s.reply("198.51.100.23")  # doctest: +SKIP
    """

    def __init__(
        self,
        api: AIApi,
        agent: str,
        *,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> None:
        self.api = api
        self.agent = agent
        self.session_id = session_id or str(_uuidlib.uuid4())
        self.context: dict[str, Any] = dict(context or {})
        self.user_id = user_id
        self.previous_response_id = ""
        self.request_id = ""
        self.playbook_context: dict[str, Any] | None = None
        self.turns: list[AgentTurn] = []

    @property
    def last(self) -> AgentTurn | None:
        return self.turns[-1] if self.turns else None

    def start(self, question: str, *, extra_context: dict[str, Any] | None = None) -> AgentTurn:
        """Send one turn without waiting; returns the handle (``task_id``/``status``)."""
        payload: dict[str, Any] = {
            "question": question,
            "previous_response_id": self.previous_response_id,
            "request_id": self.request_id,
            "context": {**self.context, **(extra_context or {})},
        }
        if self.user_id:
            payload["userId"] = self.user_id
        resp = self.api.client.post(
            f"/api/ai/agents/{self.agent}/trigger",
            data=payload,
            headers={"X-CHAT-SESSION-ID": self.session_id},
        )
        return AgentTurn.model_validate(resp if isinstance(resp, dict) else {})

    def ask(
        self,
        question: str,
        *,
        wait: bool = True,
        interval: float = 5.0,
        timeout: float = 600.0,
        extra_context: dict[str, Any] | None = None,
    ) -> AgentTurn:
        """Send ``question`` and (by default) wait for the turn's result.

        On timeout the turn comes back with its non-terminal status (``pending``
        / ``inprogress``) instead of raising -- an agent stuck on a tool call
        looks exactly like that, so check ``turn.status`` before trusting
        ``turn.answer``.
        """
        handle = self.start(question, extra_context=extra_context)
        if not wait or not handle.task_id:
            return self._record(handle)
        deadline = time.monotonic() + timeout
        status = handle.status
        while status not in _TURN_DONE and time.monotonic() < deadline:
            time.sleep(interval)
            status = self.api.get_status(handle.task_id)
        if status not in _TURN_DONE:
            handle.status = status
            return self._record(handle)
        resp = self.api.client.get(f"/api/ai/agents/{handle.task_id}/result")
        turn = AgentTurn.model_validate(resp if isinstance(resp, dict) else {})
        turn.task_id = handle.task_id
        if not turn.status:
            turn.status = status
        return self._record(turn)

    def reply(self, text: str, **kwargs: Any) -> AgentTurn:
        """Answer the open clarification (or any follow-up) on the same request."""
        return self.ask(text, **kwargs)

    def approve(self, **kwargs: Any) -> AgentTurn:
        """Approve the pending state-changing action. Sent as text; the agent parses it."""
        return self.ask("approve", **kwargs)

    def deny(self, **kwargs: Any) -> AgentTurn:
        """Deny the pending state-changing action."""
        return self.ask("deny", **kwargs)

    def generate_steps(self, **kwargs: Any) -> AgentTurn:
        """playbook-generator: turn the agreed outline into designer steps.

        Sends ``"generate steps"`` with the ``playbook_context`` the last outline
        turn returned, as the playbook designer does. The result's
        ``playbook_steps`` is what the designer pastes in; nothing is saved. If
        ``turn.is_user_input_needed``, answer with :meth:`ask` and call this again.

        Expensive: one LLM call per step, each resending the whole context
        (a 7-step playbook used about 330k FortiAI tokens on 8.0.1).
        """
        if not self.playbook_context:
            raise ValueError("no playbook_context yet: ask for an outline first")
        return self.ask("generate steps", extra_context={"playbook_context": self.playbook_context}, **kwargs)

    def trace(self) -> ExecutionTree | None:
        """Execution tree of the last turn (its trace id is the task id)."""
        if not self.last or not self.last.task_id:
            return None
        return self.api.traces.execution_tree(self.last.task_id)

    def _record(self, turn: AgentTurn) -> AgentTurn:
        if turn.response_id:
            self.previous_response_id = turn.response_id
        if turn.request_id:
            self.request_id = turn.request_id
        if turn.playbook_context:
            self.playbook_context = turn.playbook_context
        elif turn.playbook_steps is not None:
            self.playbook_context = None
        self.turns.append(turn)
        return turn


class AIApi(BaseAPI):
    """Drive the FortiAI agentic investigation service and its configuration."""

    # ----------------------------------------------------------- enablement
    def features_enabled(self) -> bool:
        """Return whether FortiAI features are enabled in System Settings.

        Reads ``publicValues.ai_feature.enable`` from the root settings record.
        """
        ai = (self.client.system_settings.get_public_values() or {}).get("ai_feature") or {}
        return bool(ai.get("enable"))

    def enable_features(self, enabled: bool = True, *, modified_by: str | None = None) -> dict[str, Any]:
        """Enable (or disable) FortiAI features -- the AI terms-acceptance gate.

        This is the programmatic equivalent of toggling *Enable AI Features* in
        **System Settings**; FortiSOAR records it as
        ``publicValues.ai_feature`` and treats enabling it as acceptance of the
        AI terms & conditions. Must be done once before any investigation,
        LLM-config or MCP call will succeed.

        Like the UI's *Acknowledge* button, it also stamps ``lastModifiedDate``
        (``MM/dd/yyyy``, the UI's ``DEFAULT_DATE_FORMAT.DATE``) -- the System
        Settings page renders "On {date}, user {username} acknowledged ..." from
        these two fields, and shows no acknowledgement line without them.

        Args:
            enabled: ``True`` to turn features on (default), ``False`` to disable.
            modified_by: optional display name stamped as ``lastModifiedBy``.

        Returns:
            The updated root ``SystemSettings`` record.
        """
        patch: dict[str, Any] = {
            "ai_feature": {
                "enable": bool(enabled),
                "lastModifiedDate": date.today().strftime("%m/%d/%Y"),
            }
        }
        if modified_by:
            patch["ai_feature"]["lastModifiedBy"] = modified_by
        return self.client.system_settings.update(patch)

    # ----------------------------------------------------------- traces / chat (8.0.1)
    @property
    def insights(self) -> AIInsightsAPI:
        """AI Insights -- see :class:`AIInsightsAPI`."""
        api = self.__dict__.get("_insights")
        if api is None:
            api = self.__dict__["_insights"] = AIInsightsAPI(self.client)
        return api

    @property
    def traces(self) -> AITracesAPI:
        """Agent traceability -- see :class:`AITracesAPI`."""
        api = self.__dict__.get("_traces")
        if api is None:
            api = self.__dict__["_traces"] = AITracesAPI(self.client)
        return api

    def chat(
        self,
        *,
        agent: str = "conversation",
        context: dict[str, Any] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> AgentSession:
        """Open a chat session with the SOC chat assistant (the Conversation Agent).

        This is the aiAssistant widget's path: ``POST /api/ai/agents/conversation/trigger``.
        (``/api/ai/chat/`` also exists but is not what the UI calls, and its
        request shape does not match the agent's.)

        Args:
            agent: agent name to talk to; ``"conversation"`` is the default SOC
                chat. ``"playbook-generator"`` and ``"connector-generation"`` use
                the same protocol.
            context: page context sent with every turn, e.g.
                ``{"pageName": "alerts", "userId": "<person uuid>",
                "recordIRI": "/api/3/alerts/<uuid>"}``.
            session_id: reuse an existing ``X-CHAT-SESSION-ID``; a fresh one by default.
            user_id: login id sent as ``userId`` (the widget sends the login name).
        """
        return AgentSession(self, agent, session_id=session_id, context=context, user_id=user_id)

    def orchestrate(
        self,
        *,
        context: dict[str, Any] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> AgentSession:
        """Open a session with the Orchestrator Agent (``orchestrator``).

        The orchestrator plans one request across SOPs and agents, pauses for
        clarification or approval (``turn.is_paused``), and resumes on
        :meth:`AgentSession.reply` / :meth:`AgentSession.approve` /
        :meth:`AgentSession.deny`. On 8.0.1 the Conversation Agent does not call
        it, so this direct trigger is the only way to reach it.
        """
        return self.chat(agent="orchestrator", context=context, session_id=session_id, user_id=user_id)

    def playbook_assistant(self, *, session_id: str | None = None, user_id: str | None = None) -> AgentSession:
        """Open a session with the playbook designer's assistant (``playbook-generator``).

        :meth:`AgentSession.ask` with a description returns an outline (and a
        ``playbook_context``); :meth:`AgentSession.generate_steps` turns it into
        designer steps. The agent's tools are read-only and nothing is saved.

        Example:
            >>> pb = client.ai.playbook_assistant()  # doctest: +SKIP
            >>> outline = pb.ask("On a new Critical alert, look up the source IP in VirusTotal ...")  # doctest: +SKIP
            >>> steps = pb.generate_steps()  # doctest: +SKIP
            >>> steps.playbook_steps  # doctest: +SKIP
        """
        return self.chat(
            agent="playbook-generator",
            context={"pageName": "main.playbookDetail"},
            session_id=session_id,
            user_id=user_id,
        )

    def connector_assistant(self, *, session_id: str | None = None, user_id: str | None = None) -> AgentSession:
        """Open a session with the connector wizard's assistant (``connector-generation``).

        A guided conversation: describe the API, confirm each step, and it
        writes ``info.json`` / ``connector.py`` / ``operations.py`` and offers to
        import them. On 8.0.1 the import step fails on the agent's own
        ``info.json`` serialization; ask it to show the files and install them
        with :meth:`~pyfsr.api.connectors.ConnectorsAPI.install_from_dir` instead.
        """
        return self.chat(
            agent="connector-generation",
            context={"pageName": "main.marketplace.workspace"},
            session_id=session_id,
            user_id=user_id,
        )

    # ----------------------------------------------------------- investigation
    def start_alert_investigation(self, alert: dict[str, Any] | str, *, link: bool = True) -> InvestigationHandle:
        """Kick off an asynchronous AI investigation of one alert.

        ``alert`` may be the full alert record (a dict, as returned by
        ``client.alerts.get(...)``) or a record reference (``"<uuid>"``,
        ``"alerts:<uuid>"`` or a full ``/api/3/alerts/<uuid>`` IRI), in which
        case the record is fetched first. The whole alert JSON is posted to the
        triage pipeline.

        When ``link`` is true (default), the returned ``task_id`` is written back
        to the alert's :data:`ALERT_TRIAGE_TASK_KEY` (``triagetaskid``) field,
        exactly as the FortiSOAR UI does -- this is what makes
        :meth:`get_investigation_for_alert` able to recover the investigation
        later. The write is best-effort: if the alert uuid can't be determined it
        is skipped silently (e.g. an alert dict with no ``@id``/``uuid``).

        Returns the pipeline handle ``{"task_id": ..., "status": "pending"}``.
        Poll it with :meth:`get_status` / :meth:`get_result`, or pass the alert
        to :meth:`investigate_alert` to do both in one call.
        """
        if isinstance(alert, str):
            alert = self._fetch_alert(alert)
        resp = self.client.post("/api/ai/triage/alert", data=alert)
        started = InvestigationHandle.model_validate(resp if isinstance(resp, dict) else {})
        if link:
            uuid = self._alert_uuid(alert)
            if started.task_id and uuid:
                self.client.alerts.update(uuid, {ALERT_TRIAGE_TASK_KEY: started.task_id})
        return started

    def get_investigation_for_alert(self, alert: dict[str, Any] | str) -> str | None:
        """Return the ``task_id`` of an alert's current investigation, or ``None``.

        Reads the alert's :data:`ALERT_TRIAGE_TASK_KEY` (``triagetaskid``) field --
        the direct alert→investigation link FortiSOAR persists when triage starts.
        ``alert`` may be a record (dict) or a reference (uuid / ``"alerts:<uuid>"``
        / IRI), in which case the alert is fetched first.

        Returns ``None`` when no investigation has run (or the field is absent
        because FortiAI isn't installed). Note the field holds only the *latest*
        investigation; use :meth:`find_investigations` to recover earlier ones.
        Feed the result to :meth:`get_status` / :meth:`get_result`.
        """
        rec = self._fetch_alert(alert) if isinstance(alert, str) else alert
        task_id = (rec or {}).get(ALERT_TRIAGE_TASK_KEY)
        return task_id or None

    def get_alert_investigation_status(self, alert: dict[str, Any] | str) -> InvestigationHandle | None:
        """Return ``{"task_id", "status"}`` for an alert's current investigation.

        Convenience over :meth:`get_investigation_for_alert` +
        :meth:`get_status`. Returns ``None`` when the alert has no investigation
        linked (so callers can distinguish "never investigated" from a real
        status). ``status`` is one of ``pending`` / ``inprogress`` /
        ``completed`` / ``failed`` (see :data:`TERMINAL_STATUSES`).
        """
        task_id = self.get_investigation_for_alert(alert)
        if not task_id:
            return None
        return InvestigationHandle(task_id=task_id, status=self.get_status(task_id))

    def get_status(self, task_id: str) -> str:
        """Return the current pipeline status for a triage task.

        While running the pipeline reports ``"pending"`` then ``"inprogress"``;
        it ends on a terminal status -- ``"completed"`` or ``"failed"`` (see
        :data:`TERMINAL_STATUSES`). Returns ``""`` if the status can't be read.
        """
        resp = self.client.get(f"/api/ai/agents/{task_id}/status")
        return (resp or {}).get("status", "") if isinstance(resp, dict) else ""

    def get_result(self, task_id: str) -> InvestigationResult:
        """Fetch the full investigation result/verdict for a triage task.

        The payload carries the per-stage ``phases`` (normalization →
        hypothesis → planning → evidence → verdict), any ``logs``, and -- once
        ``status == "completed"`` -- the synthesized verdict/summary. While the
        pipeline is still running this returns the partial progress so far.
        """
        resp = self.client.get(f"/api/ai/agents/{task_id}/result")
        return InvestigationResult.model_validate(resp if isinstance(resp, dict) else {"result": resp})

    def investigation_questions(self, task_id: str) -> list[InvestigationQuestion]:
        """Return the investigation's question-by-question evidence.

        This is the data behind the UI's *Investigation Questions* panel -- one
        entry per question the agents asked, shaped as::

            {"index", "question", "agent", "input", "response", "evidence",
             "supports": [hyp_id, ...], "weakens": [hyp_id, ...],
             "information_type", "status"}

        ``input`` is the agent's tool input (``params``), ``response`` its answer
        (``result``), and ``evidence`` the natural-language justification derived
        from the tool output. ``supports``/``weakens`` are the hypothesis ids this
        answer votes for/against -- the link into the weighting (see
        :meth:`hypothesis_evidence`). Each entry's ``agent`` (e.g. *Threat
        Intelligence Provider*, *Query SIEM*) is the agent that answered it;
        which MCP tool that agent called is recoverable via
        :meth:`attribute_tool_calls` (joined on the shared IOC value in ``input``).
        """
        logs = self.get_result(task_id).logs or []
        out: list[InvestigationQuestion] = []
        for log in logs:
            if not isinstance(log, dict):
                continue
            out.append(
                InvestigationQuestion(
                    index=log.get("index"),
                    question=log.get("question"),
                    agent=log.get("agent_label") or log.get("agent_hint"),
                    input=log.get("params"),
                    response=log.get("result"),
                    evidence=log.get("evidence"),
                    supports=[str(h) for h in (log.get("supports") or [])],
                    weakens=[str(h) for h in (log.get("weakens") or [])],
                    information_type=log.get("primary_information_type"),
                    status=log.get("status"),
                )
            )
        return out

    def hypothesis_evidence(self, task_id: str) -> dict[str, Any]:
        """Reconstruct how tool-derived evidence weighted each hypothesis → verdict.

        This is the **provenance/weighting view**: it shows, per hypothesis, the
        questions whose evidence supported or weakened it, alongside the
        hypothesis's resolved status and the final verdict -- i.e. proof that the
        investigation's conclusion is grounded in the gathered evidence rather
        than asserted. Returns::

            {"classification": "Malicious",
             "key_findings": [...],
             "hypotheses": [
               {"id", "name", "status", "attention_needed",
                "support_count", "weaken_count",
                "supported_by": [{"index", "question", "agent", "evidence"}, ...],
                "weakened_by":  [{"index", "question", "agent", "evidence"}, ...]},
               ...]}

        Each ``supported_by``/``weakened_by`` entry is a question from
        :meth:`investigation_questions`, so you can trace verdict → hypothesis →
        the exact evidence (and via the agent + IOC, the tool call) behind it.
        """
        result = self.get_result(task_id)
        questions = self.investigation_questions(task_id)
        summary = result.summary if isinstance(result.summary, dict) else {}
        hyps: list[dict[str, Any]] = []
        for hyp in result.hypotheses or []:
            if not isinstance(hyp, dict):
                continue
            hid = str(hyp.get("id"))
            supported = [
                {k: getattr(q, k) for k in ("index", "question", "agent", "evidence")}
                for q in questions
                if hid in q.supports
            ]
            weakened = [
                {k: getattr(q, k) for k in ("index", "question", "agent", "evidence")}
                for q in questions
                if hid in q.weakens
            ]
            hyps.append(
                {
                    "id": hid,
                    "name": hyp.get("name"),
                    "status": hyp.get("intentStatus"),
                    "attention_needed": hyp.get("attentionNeeded"),
                    "support_count": len(supported),
                    "weaken_count": len(weakened),
                    "supported_by": supported,
                    "weakened_by": weakened,
                }
            )
        return {
            "classification": summary.get("classification"),
            "key_findings": summary.get("key_findings"),
            "hypotheses": hyps,
        }

    def wait_for_result(self, task_id: str, *, interval: float = 5.0, timeout: float = 600.0) -> InvestigationResult:
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
        if not result.status:
            result.status = status
        return result

    def investigate_alert(
        self,
        alert: dict[str, Any] | str,
        *,
        wait: bool = False,
        interval: float = 5.0,
        timeout: float = 600.0,
    ) -> InvestigationHandle | InvestigationResult:
        """Start an investigation and (optionally) block until it finishes.

        Convenience over :meth:`start_alert_investigation` +
        :meth:`wait_for_result`. With ``wait=False`` (default) returns the
        ``{"task_id", "status"}`` handle immediately; with ``wait=True`` polls
        and returns the final result payload (including its ``task_id``).
        """
        started = self.start_alert_investigation(alert)
        task_id = started.task_id
        if not wait or not task_id:
            return started
        result = self.wait_for_result(task_id, interval=interval, timeout=timeout)
        if not result.task_id:
            result.task_id = task_id
        return result

    def agent_input_schema(self, agent_name: str, version: str = "1.0.0") -> dict[str, Any]:
        """Return an agent's declared input contract (its ``inputformat``).

        Every agent publishes the exact keys :meth:`run_agent` expects, with per-key
        ``required`` flags, enums and examples -- so you never have to guess the payload.
        The shape varies by agent: ``ioc-enrichment`` wants ``{"question", "ioc":
        [{"type", "value"}]}``, ``alert-investigation`` wants ``{"data": <raw alert>}``.

        Example:
            >>> schema = client.ai.agent_input_schema("ioc-enrichment")  # doctest: +SKIP
            >>> sorted(schema)  # doctest: +SKIP
            ['ioc', 'question']
        """
        agent = self.get_agent(agent_name, version)
        raw = agent.inputformat if isinstance(agent.inputformat, dict) else {}
        return dict(raw)

    @staticmethod
    def _missing_required_inputs(schema: dict[str, Any], data: dict[str, Any]) -> list[str]:
        """Names of ``required`` keys in ``schema`` that ``data`` does not supply.

        Only entries whose spec is a dict carrying ``required: true`` are enforced --
        several agents declare their inputs as plain strings (a description, with no
        required flag), and those are advisory only.
        """
        return [
            key for key, spec in schema.items() if isinstance(spec, dict) and spec.get("required") and key not in data
        ]

    def run_agent(
        self,
        agent_name: str,
        data: dict[str, Any],
        *,
        version: str = "1.0.0",
        validate: bool = True,
        wait: bool = False,
        interval: float = 5.0,
        timeout: float = 600.0,
    ) -> InvestigationHandle | AgentRunResult:
        """Trigger a single named agent (e.g. ``"ioc-enrichment"``) directly.

        Unlike :meth:`investigate_alert`, this runs *one* agent and answers one
        question -- no hypothesis/planning/verdict pipeline. ``data`` is the agent's
        own input contract; fetch it with :meth:`agent_input_schema`.

        Args:
            agent_name: installed agent name, e.g. ``"ioc-enrichment"``.
            data: payload matching the agent's ``inputformat``.
            version: agent version to validate ``data`` against (default ``"1.0.0"``).
            validate: when True (default), check ``data`` against the agent's declared
                required keys and raise :class:`ValueError` before spending an LLM call.
                Set False to skip the extra ``GET /api/ai/agent/{name}/{version}``.
            wait: block until the run reaches a terminal status and return the result.
            interval: seconds between status polls when ``wait=True``.
            timeout: give up polling after this many seconds when ``wait=True``.

        Returns:
            An :class:`~pyfsr.models.InvestigationHandle` (``{"task_id", "status"}``),
            or an :class:`~pyfsr.models.AgentRunResult` when ``wait=True``.

        Raises:
            ValueError: ``validate=True`` and ``data`` omits a required input key.

        Example:
            >>> result = client.ai.run_agent(  # doctest: +SKIP
            ...     "ioc-enrichment",
            ...     {"question": "Is this IP known malicious?",
            ...      "ioc": [{"type": "IP Address", "value": "8.8.8.8"}]},
            ...     wait=True,
            ... )
            >>> result.answer, result.confidence  # doctest: +SKIP
            ('No', '90%')
        """
        if validate:
            missing = self._missing_required_inputs(self.agent_input_schema(agent_name, version), data)
            if missing:
                raise ValueError(
                    f"agent {agent_name!r} requires input key(s) {missing} -- "
                    f"see client.ai.agent_input_schema({agent_name!r}) for the full contract"
                )
        # NOTE: the trigger must go to /api/ai/agents/... (plural). The fsr-ai service
        # mounts the same router under both /ai/triage and /ai/agents, but the PHP
        # front door only authorises `^agents?/(.*)/trigger` -- a POST to
        # /api/ai/triage/{name}/trigger matches no permission group and is rejected
        # with a bare "Access Denied" regardless of the caller's role.
        resp = self.client.post(f"/api/ai/agents/{agent_name}/trigger", data=data)
        handle = InvestigationHandle.model_validate(resp if isinstance(resp, dict) else {})
        if not wait or not handle.task_id:
            return handle
        return self.wait_for_agent_result(handle.task_id, interval=interval, timeout=timeout)

    def get_agent_result(self, task_id: str) -> AgentRunResult:
        """Fetch a single-agent run's result (``answer`` / ``evidence`` / ``confidence``).

        Same endpoint as :meth:`get_result`, typed for the single-agent shape -- use
        this for :meth:`run_agent` tasks and :meth:`get_result` for investigations.
        """
        resp = self.client.get(f"/api/ai/agents/{task_id}/result")
        return AgentRunResult.model_validate(resp if isinstance(resp, dict) else {})

    def wait_for_agent_result(self, task_id: str, *, interval: float = 5.0, timeout: float = 600.0) -> AgentRunResult:
        """Poll a single-agent task to a terminal status, then return its result.

        Mirrors :meth:`wait_for_result` (which returns the investigation shape). On
        timeout the latest result is returned with a non-terminal ``status`` rather
        than raising -- check ``result.status`` before trusting ``answer``.
        """
        deadline = time.monotonic() + timeout
        status = self.get_status(task_id)
        while status not in TERMINAL_STATUSES and time.monotonic() < deadline:
            time.sleep(interval)
            status = self.get_status(task_id)
        result = self.get_agent_result(task_id)
        if not result.status:
            result.status = status
        if not result.task_id:
            result.task_id = task_id
        return result

    def submit_feedback(self, task_id: str, feedback: dict[str, Any]) -> dict[str, Any]:
        """Submit analyst feedback / acceptance on a triage verdict."""
        return self.client.post(f"/api/ai/agents/{task_id}/acceptance", data=feedback)

    # ----------------------------------------------------------- LLM providers
    def list_providers(self) -> list[LLMProvider]:
        """List the allowed LLM providers (the installed AI solution packs)."""
        return [LLMProvider.model_validate(p) for p in _as_list(self.client.get("/api/ai/llm/allowed-providers"))]

    def list_llm_configs(self) -> list[LLMConfig]:
        """List the configured reasoning profiles (e.g. *Low* / *High Reasoning*)."""
        return [LLMConfig.model_validate(c) for c in _as_list(self.client.get("/api/ai/llm/config"))]

    def get_llm_config(self, uuid: str) -> LLMConfig:
        """Fetch one reasoning-profile config by uuid."""
        resp = self.client.get(f"/api/ai/llm/config/{uuid}")
        return LLMConfig.model_validate(resp if isinstance(resp, dict) else {})

    def create_llm_config(self, configs: list[dict[str, Any]]) -> Any:
        """Create one or more reasoning-profile configs (``POST /api/ai/llm/config``).

        The endpoint takes a *list* of config objects, mirroring the UI's bulk
        save; a single dict is wrapped automatically.

        Note: the endpoint upserts by UUID -- if ``uuid`` is set and exists, the
        profile is updated; if ``uuid`` is ``None`` a new one is created. However,
        re-POSTing the same ``name`` with a *different* ``uuid`` will 500 on a
        name-collision. For idempotent creates, use :meth:`upsert_llm_config`.
        """
        body = configs if isinstance(configs, list) else [configs]
        return self.client.post("/api/ai/llm/config", data=body)

    def upsert_llm_config(
        self,
        name: str,
        *,
        provider: str,
        modelname: str | None = None,
        apikey: str | None = None,
        baseurl: str | None = None,
        config: dict[str, Any] | None = None,
        isdefault: bool = False,
        active: bool = True,
        uuid: str | None = None,
    ) -> LLMConfig:
        """Idempotently create-or-update an LLM reasoning profile by name.

        Searches existing configs for a matching ``name``. If found, reuses its
        UUID (so the backend upserts in place). If not found, uses the supplied
        ``uuid`` (or generates one). Then ``POST /api/ai/llm/config`` -- safe to
        re-run any number of times.

        Args:
            name: profile display name (e.g. ``"Low Reasoning"``).
            provider: one of ``openai``, ``anthropic``, ``gemini``, ``fortisoar``.
            modelname: model identifier (e.g. ``"gpt-4.1"``).
            apikey: API key for native providers (``openai``/``anthropic``/``gemini``).
                Ignored for ``fortisoar`` (auth comes from the connector).
            baseurl: base URL for native providers (e.g.
                ``"https://api.openai.com/v1"``). Stored, but fsr-ai 8.0.0's
                native clients pass only the API key -- reach Azure or a
                self-hosted endpoint through a ``fortisoar`` connector profile. Stored, but fsr-ai 8.0.0's
                native clients pass only the API key -- reach Azure or a
                self-hosted endpoint through a ``fortisoar`` connector profile.
            config: provider-specific config dict. For ``fortisoar``:
                ``{"connector_name": "openai", "connector_config_id": "<uuid>"}``.
                For native providers: ``{"temperature": 0.1}``.
            isdefault: mark this as the default profile.
            active: mark this profile as active.
            uuid: explicit UUID for a new profile. If omitted and the name
                doesn't exist, a random UUID is generated.

        Returns:
            The saved :class:`~pyfsr.models.LLMConfig`.

        Example:
            >>> client.ai.upsert_llm_config(  # doctest: +SKIP
            ...     "OpenAI Direct",
            ...     provider="openai",
            ...     modelname="gpt-4.1",
            ...     apikey="sk-...",
            ...     baseurl="https://api.openai.com/v1",
            ...     config={"temperature": 0.1},
            ... )
        """
        existing = self.list_llm_configs()
        found_uuid = uuid
        for c in existing:
            if c.name == name:
                found_uuid = str(c.uuid)
                break

        if not found_uuid:
            import uuid as _uuid

            found_uuid = str(_uuid.uuid4())

        payload = [
            {
                "uuid": found_uuid,
                "name": name,
                "isdefault": isdefault,
                "active": active,
                "provider": provider,
                "modelname": modelname,
                "apikey": apikey,
                "baseurl": baseurl,
                "config": config or {},
            }
        ]
        self.client.post("/api/ai/llm/config", data=payload)
        return self.get_llm_config(found_uuid)

    def token_balance(self, llm_config: str | None = None) -> FortiAITokenBalance:
        """FortiAI token entitlement and what is left (``get_token_balance_info``).

        Runs the ``fortinet-fortiai-proxy`` connector's balance operation with
        the connector configuration behind ``llm_config`` (a profile name or
        uuid; default: the default profile). The balance is per device, so every
        profile on the appliance draws from the same pool. Reading it before and
        after an investigation gives the tokens FortiAI actually metered.

        Raises:
            ValueError: the profile is not backed by the FortiAI proxy connector
                (a native ``openai``/``anthropic``/``gemini`` profile bills the
                provider account, which FortiSOAR cannot read).
        """
        configs = self.list_llm_configs()
        if llm_config:
            profile = next((c for c in configs if llm_config in (c.name, c.uuid)), None)
        else:
            profile = next((c for c in configs if c.isdefault), None)
        if profile is None:
            raise ValueError(f"no LLM profile {llm_config or '(default)'!r}")
        connector = profile.config.get("connector_name")
        if profile.provider != "fortisoar" or connector != FORTIAI_PROXY_CONNECTOR:
            raise ValueError(
                f"LLM profile {profile.name!r} uses provider {profile.provider!r}"
                f" (connector {connector!r}), not the FortiAI proxy -- no token balance to read"
            )
        result = self.client.connectors.execute(
            connector, "get_token_balance_info", config=profile.config.get("connector_config_id"), params={}
        )
        data = getattr(result, "data", None)
        return FortiAITokenBalance.model_validate(data if isinstance(data, dict) else {})

    def verify_llm_config(self, uuid: str, *, model_id: str | None = None) -> dict[str, Any]:
        """Verify a saved LLM config on the live appliance.

        Calls ``GET /api/ai/llm/config/{uuid}/verify`` -- the only reachable
        verification endpoint (``POST /api/ai/llm/test`` is not authorised
        through the API gateway).

        .. note::
           The handler (identical in fsr-ai 8.0.0 and 8.0.1) is
           ``verify_config(model_id)`` mounted on a ``{uuid}`` path, so FastAPI
           ignores the path and requires a ``?model_id=`` **query** parameter,
           then looks the config up by it -- it wants the config **UUID**, not
           a model name (a model name 500s on a uuid cast). The path-only form
           is tried first (in case a later build fixes the binding); on a 422
           that names ``model_id`` the call is retried with
           ``?model_id=<uuid>``. Either way the config's own ``modelname`` is
           what gets tested, and the ``model_id`` argument stays **accepted and
           ignored**.

           Only the OpenAI client implements the connection test (8.0.0 and
           8.0.1): a ``fortisoar`` (FortiAI proxy), ``anthropic`` or ``gemini``
           profile raises ``NotImplementedError`` server-side, surfaced here as
           an :class:`~pyfsr.exceptions.APIError` (HTTP 500). That is the
           appliance, not the config -- :meth:`test_llm_config` hits the same
           code path and fails the same way.

        Args:
            uuid: the LLM config UUID (from :meth:`list_llm_configs`).
            model_id: ignored -- see the note above. The config's own
                ``modelname`` is what gets tested.

        Returns:
            The verification result dict from the appliance.
        """
        endpoint = f"/api/ai/llm/config/{uuid}/verify"
        try:
            return self.client.get(endpoint)
        except APIError as err:
            if err.status_code != 422 or "model_id" not in str(err):
                raise
        return self.client.get(endpoint, params={"model_id": uuid})

    def test_llm_config(
        self,
        *,
        name: str,
        provider: str,
        modelname: str | None = None,
        apikey: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Test an LLM config *before* saving it (``POST /api/ai/llm/config/verify``).

        Sends a full ``LLMConfigDTO`` body; fsr-ai builds a client and pings the
        provider. This is the pre-save verification the UI uses.

        Args:
            name: profile name (checked for uniqueness on the appliance).
            provider: one of ``openai``, ``anthropic``, ``gemini``, ``fortisoar``.
            modelname: model identifier (e.g. ``"gpt-4.1"``).
            apikey: API key for native providers.
            config: provider-specific config dict.

        Returns:
            The config dict if verification passed; raises on failure.
        """
        body: dict[str, Any] = {
            "name": name,
            "provider": provider,
            "modelname": modelname,
            "apikey": apikey,
            "config": config or {},
        }
        return self.client.post("/api/ai/llm/config/verify", data=body)

    def delete_llm_config(self, uuid: str) -> None:
        """Delete a reasoning-profile config by uuid.

        .. warning::
           On fsr-ai 8.0.0 the ``DELETE /api/ai/llm/config/{uuid}`` handler is a
           **known no-op bug** -- it calls ``get_llm_config_by_uuid`` (a read)
           instead of the service's ``delete_llm_configuration``. The profile is
           not deleted and the endpoint returns 200. To actually remove a
           profile you must delete it from the ``venom.llm_configuration`` table
           directly (requires root on the appliance) or overwrite it via an
           upsert. See :meth:`upsert_llm_config` for the safe overwrite path.
        """
        self.client.delete(f"/api/ai/llm/config/{uuid}")

    # ----------------------------------------------------------- MCP servers
    def list_mcp_servers(self) -> list[MCPServerRef]:
        """List registered MCP servers (id + name) the AI agents can be granted."""
        return [MCPServerRef.model_validate(m) for m in _as_list(self.client.get("/api/ai/mcp"))]

    def mcp_status(self) -> list[MCPServerStatus]:
        """Runtime health of every registered MCP server (``GET /api/ai/mcp/status``).

        Returns one :class:`~pyfsr.models.MCPServerStatus` per registered server -- the same
        green/red liveness probe the agent UI uses. ``valid`` is the connectivity
        verdict; ``error`` carries the failure reason when it is not. Complements
        :meth:`list_mcp_servers` (id+name only) and :meth:`mcp_configs` (full
        records). Live-verified on 8.0.0.

        Example:
            >>> for s in client.ai.mcp_status():  # doctest: +SKIP
            ...     if not s.valid:
            ...         print(f"{s.name}: {s.error}")
        """
        return [MCPServerStatus.model_validate(m) for m in _as_list(self.client.get("/api/ai/mcp/status"))]

    def validate_mcp_server(self, config: dict[str, Any]) -> MCPValidateResult:
        """Probe an MCP-server config *before* persisting it.

        Opens a connection to the server's ``url`` and runs ``tools/list``,
        returning ``{"valid": bool, "tools": [...], "message": ...}``. Inspect
        ``tools`` for the names you'll later allowlist per agent. Always call
        this before :meth:`register_mcp_server` and do not persist on failure.
        """
        resp = self.client.post("/api/ai/mcp/validate", data=config)
        return MCPValidateResult.model_validate(resp if isinstance(resp, dict) else {})

    def list_mcp_tools(self, config: dict[str, Any]) -> list[str]:
        """Return the tool *names* an MCP server advertises (its ``tools/list``).

        Thin convenience over :meth:`validate_mcp_server` -- opens the connection,
        runs ``tools/list``, and returns just the tool names. Use it to learn a
        server's tool surface (e.g. which tools belong to FortiSIEM) so you can
        attribute observed :meth:`tool_usage` back to the server that owns them.

        ``config`` is a full MCP-server config (``url`` + ``authentication``),
        exactly as passed to :meth:`validate_mcp_server`; for a bearer server
        whose token has expired, mint a fresh one first (FortiSOAR stores the
        credential write-only and won't re-probe with it).
        """
        result = self.validate_mcp_server(config)
        return [t.name for t in result.tools if t.name]

    def mcp_configs(self) -> list[MCPServerConfig]:
        """Return the full registered MCP-server records (``/api/3/mcp_configurations``).

        Unlike :meth:`list_mcp_servers` (id + name only) these carry ``url``,
        ``transport``, ``type`` and the stored ``authentication`` (a JSON
        *string*). Used by :meth:`mcp_tool_catalog` to re-probe each server.
        """
        return [MCPServerConfig.model_validate(m) for m in _as_list(self.client.get("/api/3/mcp_configurations"))]

    def get_mcp_config(self, name_or_uuid: str) -> MCPServerConfig:
        """Resolve one registered MCP server by **name** or **uuid**.

        A uuid is fetched directly; a name uses the collection's server-side
        ``name`` filter (one round-trip) rather than scanning :meth:`mcp_configs`.

        Args:
            name_or_uuid: the server's ``name`` (e.g. ``"Bridge: FortiSIEM"``) or uuid.

        Raises:
            ValueError: if no MCP configuration matches.
        """
        if _is_uuid(name_or_uuid):
            return MCPServerConfig.model_validate(self.client.get(f"/api/3/mcp_configurations/{name_or_uuid}"))
        # ``extract_members``, not ``_as_list``: this is a collection GET, and
        # ``_as_list`` coerces a bare dict to ``[resp]`` -- which would turn an empty
        # response into a phantom member and mask a genuine "not found".
        members = extract_members(self.client.get("/api/3/mcp_configurations", params={"name": name_or_uuid}))
        for record in members:
            if isinstance(record, dict):
                return MCPServerConfig.model_validate(record)
        raise ValueError(f"MCP configuration {name_or_uuid!r} not found")

    def _resolve_registered_endpoint(self, name_or_uuid: str, token: str | None) -> tuple[str, dict[str, str], Any]:
        """Resolve a registered server to ``(url, auth_headers, verify)``.

        Decodes the record's stored ``authentication`` (a JSON string), lets
        ``token`` override its ``value`` (for a masked/rotated secret), and builds
        the request headers the same way fsr-ai does.
        """
        from .native_mcp import build_mcp_auth_headers

        rec = self.get_mcp_config(name_or_uuid)
        if not rec.url:
            raise ValueError(f"registered MCP server {name_or_uuid!r} has no url")
        auth = rec.authentication
        if isinstance(auth, str):
            auth = json.loads(auth) if auth else {}
        auth = dict(auth or {"type": "NONE"})
        if token is not None:
            auth["value"] = token
        verify = rec.get("verify", self.client.verify_ssl)
        # An OAUTH2 server's token endpoint is called with the same TLS setting as the server.
        auth.setdefault("verify", verify)
        return rec.url, build_mcp_auth_headers(auth), verify

    def list_registered_tools(self, name_or_uuid: str, *, token: str | None = None) -> list[MCPTool]:
        """List the tools a **registered** MCP server advertises, by calling it.

        Resolves the server's ``url`` + ``authentication`` from its registration
        and opens a real MCP ``tools/list`` -- the same reach fsr-ai's agent uses
        (streamable-HTTP + auth header), driven from your own process. Thinner
        than :meth:`mcp_tool_catalog` (one server, typed :class:`~pyfsr.models.MCPTool`).

        ``token`` supplies the credential value when the stored one is masked or
        you want to override it. For an ``OAUTH2`` server (8.0.1+), pass a token
        you minted; without one pyfsr mints from the stored ``token_url``/
        ``client_id``/``client_secret``, which only works if the record returns
        the secret unmasked. Needs ``pip install 'pyfsr[mcp]'``.
        """
        url, headers, verify = self._resolve_registered_endpoint(name_or_uuid, token)
        return self.client.mcp.list_tools_at(url, headers, verify=verify)

    def call_registered_tool(
        self,
        name_or_uuid: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        token: str | None = None,
    ) -> MCPToolResult:
        """Execute one tool on a **registered** MCP server (external or internal).

        This is how you invoke a registered server's tool *outside* an agent
        investigation: FortiSOAR exposes no REST endpoint that runs one (only
        ``/api/ai/mcp/validate`` -- a ``tools/list`` probe), so this resolves the
        server's ``url`` + ``authentication`` from its registration and speaks MCP
        ``tools/call`` directly over streamable-HTTP -- the same mechanism fsr-ai's
        agent uses (``langchain_mcp_adapters.MultiServerMCPClient``), reusing
        pyfsr's own raw-MCP session (no langchain dependency).

        The appliance is not in the tool-call path (it never proxies external
        servers); only the config lookup goes through it.

        Args:
            name_or_uuid: registered server ``name`` (e.g. ``"Bridge: FortiSIEM"``) or uuid.
            tool: the tool name (see :meth:`list_registered_tools`).
            arguments: tool arguments (shape is the tool's own ``input_schema``).
            token: credential value to use when the stored one is masked/rotated.

        Returns a typed :class:`~pyfsr.models.MCPToolResult` (``r.ok`` / ``r.result``
        / ``r.error``). Needs ``pip install 'pyfsr[mcp]'``.
        """
        from .native_mcp import _to_tool_result

        url, headers, verify = self._resolve_registered_endpoint(name_or_uuid, token)
        payload = self.client.mcp.call_tool_at(url, headers, tool, arguments, verify=verify)
        return _to_tool_result(payload)

    def mcp_tool_catalog(self) -> dict[str, dict[str, Any]]:
        """Map every advertised tool to the MCP server that owns it.

        Probes *each* registered MCP server's ``tools/list`` (via its stored
        config, decoding the ``authentication`` JSON string) and returns::

            {"<tool_name>": {"server", "server_uuid", "description"}, ...}

        This is the **vendor-neutral** tool→server attribution: it works for any
        registered server (FortiSIEM, a 3rd-party SIEM, internal FSR servers, …),
        not just FortiSIEM, and needs no extra credentials when the stored token
        is still valid. Servers that fail to probe (e.g. an expired bearer token)
        are skipped -- mint a fresh token and :meth:`update_mcp_server` first to
        include them.

        A tool name seen on two servers keeps the first-probed owner (collisions
        are rare; inspect :meth:`mcp_configs` if you need to disambiguate).
        """
        catalog: dict[str, dict[str, Any]] = {}
        for cfg in self.mcp_configs():
            uuid = cfg.uuid or cfg.get("id")
            name = cfg.name or uuid
            probe = cfg.to_dict(by_alias=False, exclude_none=True)
            auth = probe.get("authentication")
            if isinstance(auth, str):
                try:
                    probe["authentication"] = json.loads(auth)
                except (ValueError, TypeError):
                    pass
            try:
                result = self.validate_mcp_server(probe)
            except Exception:  # noqa: BLE001 - one unreachable server shouldn't blank the rest
                continue
            for t in result.tools:
                if t.name and t.name not in catalog:
                    catalog[t.name] = {
                        "server": name,
                        "server_uuid": uuid,
                        "description": t.description,
                    }
        return catalog

    def attribute_tool_calls(self, task_id: str, *, catalog: dict[str, dict[str, Any]] | None = None) -> list[ToolCall]:
        """Tool calls of one investigation, each tagged with its owning MCP server.

        Combines :meth:`investigation_tool_calls` (what the agents called, with
        ``tool_args``) with :meth:`mcp_tool_catalog` (who owns each tool), so each
        entry gains ``server`` / ``server_uuid``. Tools with no registered owner
        report ``server = None`` (e.g. a built-in/connector action rather than an
        MCP tool). Pass a pre-built ``catalog`` to avoid re-probing every server
        across repeated calls.

        On 8.0.1 the calls come from traces, which record the server that ran
        each one, so they are returned as-is and no catalog is built.

        Returns the :meth:`tool_usage` dicts (``tool_name``, ``tool_args``,
        ``correlation_id``, …) each extended with ``server`` and ``server_uuid``.
        """
        calls = self.investigation_tool_calls(task_id)
        if calls and all(c.source == "traces" for c in calls):
            return calls  # 8.0.1 traces already name the server that ran each call
        if catalog is None:
            catalog = self.mcp_tool_catalog()
        out: list[ToolCall] = []
        for call in calls:
            owner = catalog.get(call.tool_name) or {}
            out.append(
                ToolCall.model_validate(
                    {**call.model_dump(), "server": owner.get("server"), "server_uuid": owner.get("server_uuid")}
                )
            )
        return out

    def register_mcp_server(self, config: dict[str, Any]) -> MCPServerConfig:
        """Persist a validated MCP-server config (``POST /api/3/mcp_configurations``).

        The ``authentication`` block is encrypted server-side, so always create
        rows through this API -- never write the ``mcp_configuration`` table
        directly. Returns the created record (including its new ``uuid``).

        Note: the persistence layer stores ``authentication`` as a JSON *string*
        (the built-ins are e.g. ``'{"type":"FSR"}'``). A dict is JSON-encoded here
        automatically -- passing a raw object makes the backend stringify it to the
        literal ``"Array"``, which then breaks ``GET /api/ai/mcp/status`` for every
        server (it ``json.loads`` each row's auth).

        ``type`` defaults to ``"external"`` (what the UI sends for a user-added
        server). 8.0.1 rejects a create without it ("type: This value should not
        be blank."); 8.0.0 accepted either. Pass ``type`` explicitly for anything
        else (the built-ins are ``"internal"``, connector-hosted ones ``"connector"``).
        """
        config = dict(config)
        config.setdefault("type", "external")
        auth = config.get("authentication")
        if isinstance(auth, dict):
            config["authentication"] = json.dumps(auth)
        resp = self.client.post("/api/3/mcp_configurations", data=config)
        return MCPServerConfig.model_validate(resp if isinstance(resp, dict) else {})

    def update_mcp_server(self, uuid: str, config: dict[str, Any]) -> MCPServerConfig:
        """Update a registered MCP server (``PUT /api/3/mcp_configurations/{uuid}``).

        Use this to rotate a credential whose token expires -- e.g. re-stamping a
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
        resp = self.client.put(f"/api/3/mcp_configurations/{uuid}", data=config)
        return MCPServerConfig.model_validate(resp if isinstance(resp, dict) else {})

    def save_mcp_server(self, config: dict[str, Any], *, validate: bool = True) -> MCPServerConfig:
        """Validate then persist an MCP server -- the exact flow the FortiSOAR UI uses.

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
            if not result.valid:
                raise ValueError(f"MCP server did not validate, not saving: {result.message or result}")
        uuid = config.get("uuid")
        if uuid:
            return self.update_mcp_server(uuid, config)
        return self.register_mcp_server(config)

    def upsert_mcp_server(self, config: dict[str, Any], *, validate: bool = True) -> MCPServerConfig:
        """Create or update an MCP server **keyed by name** -- re-runnable setup.

        :meth:`save_mcp_server` routes on ``uuid`` (present → update, absent →
        create), which means a caller must look the row up by name and inject the
        uuid themselves to avoid duplicating a server on every run. This does that
        lookup: if a registered server already has the same ``name``, its uuid is
        merged into ``config`` so the existing row is updated in place; otherwise a
        new one is created. Mirrors :meth:`~pyfsr.api.connectors.ConnectorsAPI.upsert_configuration`.

        The returned record always carries a usable ``uuid`` key (back-filled from
        the matched row when the create/update response omits it), so callers can
        use ``upsert_mcp_server(cfg)["uuid"]`` directly.
        """
        name = config.get("name")
        existing_uuid = None
        if name:
            existing_uuid = next(
                (m.get("uuid") or m.get("id") for m in self.list_mcp_servers() if m.get("name") == name),
                None,
            )
        if existing_uuid:
            config = {**config, "uuid": existing_uuid}
        saved = self.save_mcp_server(config, validate=validate)
        if not saved.uuid:
            uuid = existing_uuid or uuid_from_iri(saved.get("@id"))
            if uuid:
                saved.uuid = uuid
        return saved

    def delete_mcp_server(self, uuid: str) -> None:
        """Delete a registered MCP server by uuid."""
        self.client.delete(f"/api/3/mcp_configurations/{uuid}")

    def register_and_verify(self, config: dict[str, Any]) -> dict[str, Any]:
        """Validate, register, and learn the tool list -- the one-liner for
        the "validate → check tools → upsert → print uuid" every MCP setup
        script hand-rolls today (this repo's ``fortisiem_mcp_setup_and_test.py``
        and ``register_and_call_public_mcp_server.py`` examples included).

        Raises ``ValueError`` on a failed validation (same guarantee
        :meth:`upsert_mcp_server`'s default ``validate=True`` already gives --
        this just avoids the second round-trip callers were already making to
        also learn the tool list, by reusing the one validation response for
        both).

        Returns the upserted record (with its ``uuid``) plus a ``tools`` key
        -- the tool list the validation probe reported, so callers don't need
        a follow-up :meth:`list_mcp_tools` call just to print what they
        registered.
        """
        validation = self.validate_mcp_server(config)
        if not validation.valid:
            raise ValueError(f"MCP server did not validate, not saving: {validation.message or validation}")
        saved = self.upsert_mcp_server(config, validate=False)
        return {**saved.to_dict(by_alias=False), "tools": [t.name for t in validation.tools if t.name]}

    # ------------------------------------------- connector -> MCP server
    def mcp_connector_candidates(self) -> ConnectorMcpCandidates:
        """Which installed connectors can be hosted as an MCP server (``GET /mcp/servers/connector``).

        This is the *Connector* option in the FortiSOAR UI's "Add MCP Server"
        wizard, distinct from the connect-to-an-external-server flow
        (:meth:`register_mcp_server`). Feed an ``available`` name to
        :meth:`host_connector_as_mcp_server`.
        """
        resp = self.client.get("/mcp/servers/connector", params={"restricted": "true"})
        return ConnectorMcpCandidates.model_validate(resp if isinstance(resp, dict) else {})

    def export_mcp_server_tools(self, servers: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Fetch the currently-registered tool list of one or more hosted MCP
        servers (``POST /mcp/config/export``). ``servers`` is
        ``[{"uuid", "name"}, ...]`` (see :meth:`mcp_configs`).
        """
        resp = self.client.post("/mcp/config/export", data=servers)
        return resp if isinstance(resp, list) else []

    def host_connector_as_mcp_server(
        self,
        connector_name: str,
        *,
        version: str | None = None,
        operations: list[str] | None = None,
        config_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> MCPServerConfig:
        """Convert an installed connector into a hosted MCP server -- the exact
        flow the FortiSOAR UI's "Add MCP Server → Connector" wizard runs.

        Each of the connector's operations becomes an MCP tool. By default all
        (enabled) operations are exposed; pass ``operations`` (a list of
        operation names, e.g. ``["fetch_email_new"]``) to expose a subset --
        see :meth:`~pyfsr.api.connectors.ConnectorsAPI.operations` to list them.

        If the connector requires configuration (most do), the connector's
        default configuration is used unless ``config_id`` names a specific one
        (see :meth:`~pyfsr.api.connectors.ConnectorsAPI.configurations`). A
        connector with no configured instance yet is still hosted (mirroring
        the UI), but its tools won't work until one is configured.

        Check :meth:`mcp_connector_candidates` first -- some connectors
        (internal/system ones) can never be hosted this way.

        Returns the created :class:`~pyfsr.models.MCPServerConfig`. Use
        :meth:`update_connector_mcp_server_tools` to change the exposed
        operations later, or :meth:`delete_mcp_server` to remove it entirely.
        """
        definition = self.client.connectors.definition(connector_name, version=version)
        require_configuration = definition.config_count != -1

        chosen_config = None
        if require_configuration:
            configs = self.client.connectors.configurations(connector_name)
            if config_id:
                chosen_config = next((c for c in configs if c.config_id == config_id), None)
            else:
                chosen_config = next((c for c in configs if c.default), None) or (configs[0] if configs else None)

        server_config: dict[str, Any] = {
            "name": name or definition.label or connector_name,
            "description": description if description is not None else definition.description,
            "active": True,
            "url": f"https://localhost/mcp/connector/{connector_name}/",
            "type": "connector",
            "transport": "http",
            "authentication": {"type": "FSR"},
            "metadata": {
                "connectorName": connector_name,
                "connectorLabel": definition.label,
                "configId": chosen_config.config_id if chosen_config else None,
                "configLabel": chosen_config.name if chosen_config else None,
                "requireConfiguration": require_configuration,
            },
        }
        saved = self.register_mcp_server(server_config)

        selected_ops = (
            definition.operations
            if operations is None
            else [op for op in definition.operations if op.operation in operations]
        )
        tools = [
            {
                **op.to_dict(by_alias=False, exclude_none=True),
                "addTool": True,
            }
            for op in selected_ops
        ]
        self.client.post(
            "/mcp/add/tools",
            data={
                "mcp_configuration": {"name": saved.name, "uuid": saved.uuid},
                "config": chosen_config.to_dict(by_alias=False, exclude_none=True) if chosen_config else {},
                "metadata": {
                    "name": definition.name,
                    "label": definition.label,
                    "version": definition.version,
                },
                "tools": tools,
            },
        )
        return saved

    def update_connector_mcp_server_tools(
        self,
        mcp_uuid: str,
        *,
        connector_name: str,
        version: str | None = None,
        operations: list[str],
        config_id: str | None = None,
    ) -> None:
        """Replace the tool set of an already-hosted connector MCP server
        (``PUT /mcp/tools/{uuid}``).

        ``operations`` is the *full* desired set of exposed operation names --
        any currently-exposed operation not in the list is removed
        (``remove_tools``), mirroring the UI's tool checklist. Re-resolves the
        connector definition/config the same way :meth:`host_connector_as_mcp_server`
        does; pass ``config_id`` to switch which configuration backs the server.
        """
        definition = self.client.connectors.definition(connector_name, version=version)
        require_configuration = definition.config_count != -1
        chosen_config = None
        if require_configuration:
            configs = self.client.connectors.configurations(connector_name)
            if config_id:
                chosen_config = next((c for c in configs if c.config_id == config_id), None)
            else:
                chosen_config = next((c for c in configs if c.default), None) or (configs[0] if configs else None)

        existing = self.export_mcp_server_tools([{"uuid": mcp_uuid, "name": connector_name}])
        existing_ops = {t.get("name") for t in (existing[0].get("tools") or [])} if existing else set()
        remove_tools = [op for op in existing_ops if op not in operations]

        selected_ops = [op for op in definition.operations if op.operation in operations]
        tools = [{**op.to_dict(by_alias=False, exclude_none=True), "addTool": True} for op in selected_ops]
        self.client.put(
            f"/mcp/tools/{mcp_uuid}",
            data={
                "uuid": mcp_uuid,
                "remove_tools": remove_tools,
                "config": chosen_config.to_dict(by_alias=False, exclude_none=True) if chosen_config else {},
                "metadata": {
                    "name": definition.name,
                    "label": definition.label,
                    "version": definition.version,
                },
                "tools": tools,
            },
        )

    def delete_mcp_tools(self, tools: list[dict[str, str]]) -> None:
        """Remove specific tools from a hosted MCP server (``DELETE /mcp/tools/delete``).

        ``tools`` is ``[{"uuid": <server_uuid>, "name": <operation_name>}, ...]``.
        Prefer :meth:`update_connector_mcp_server_tools` for a full tool-set
        replacement; use this for a targeted removal.
        """
        self.client.request("DELETE", "/mcp/tools/delete", data=tools)

    # ----------------------------------------------------------- agents
    def list_agents(self, **filters: Any) -> list[AgentRecord]:
        """List the installed AI agents (``GET /api/ai/agent/``).

        Optional keyword filters are passed straight through as query params --
        the service recognizes ``category``, ``status``, ``active``,
        ``installed``, ``system`` and ``publisher``. Each item is an agent
        record with ``name``, ``version``, ``label``, ``uuid``, ``active`` etc.
        """
        params = {k: v for k, v in filters.items() if v is not None}
        return [
            AgentRecord.model_validate(a) for a in _as_list(self.client.get("/api/ai/agent/", params=params or None))
        ]

    def get_agent(self, name: str, version: str) -> AgentRecord:
        """Fetch one AI agent's details (``GET /api/ai/agent/{name}/{version}``)."""
        resp = self.client.get(f"/api/ai/agent/{name}/{version}")
        return AgentRecord.model_validate(resp if isinstance(resp, dict) else {})

    # ----------------------------------------------------- import / export
    @staticmethod
    def validate_agent_package(source_dir: str) -> AgentPackage:
        """Parse + consistency-check an agent source folder without uploading.

        Returns the typed :class:`~pyfsr.models.AgentPackage` (manifest, prompts,
        MCP allowlist, file list) so you can inspect it, or raises with the exact
        defect. Run this before :meth:`import_agent` when authoring -- it catches
        the failures that would otherwise only surface when the agent runs on the
        appliance (a bad ``agentclass``, a prompt uuid the code references but
        ``prompt.yaml`` omits, a manifest icon that isn't in the bundle).
        """
        return AgentPackage.from_dir(source_dir)

    def import_agent(
        self,
        path: str,
        *,
        replace: bool = False,
        validate: bool = True,
    ) -> dict[str, Any]:
        """Install an AI agent package onto the appliance.

        ``POST /ai/agent/import`` (multipart ``file``), sent straight to fsr-ai:
        the ``/api/ai`` gateway route drops multipart bodies. ``path`` may be either
        an already-built ``.zip`` or an agent **source directory** -- a directory
        is packed on the fly with :func:`pack_agent` (which validates it first).
        Pass ``replace=True`` to overwrite an already-installed agent of the same
        name+version (``?replace=true``); without it, re-importing an existing
        name+version is rejected by the service.

        The uploaded agent lands **inactive** -- call :meth:`activate_agent` with
        its uuid (from the response or :meth:`list_agents`) to make the
        orchestrator eligible to route to it, and give it an LLM/MCP config via
        :meth:`update_agent_config` if it isn't using the default.

        Set ``validate=False`` to skip local package validation (e.g. to upload a
        vendor ``.zip`` you don't want re-inspected). Ignored when ``path`` is a
        ``.zip`` -- only source directories are validated/packed.

        Returns the service's import response (the created/updated agent record).
        """
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"agent package path not found: {path}")

        cleanup: Path | None = None
        if src.is_dir():
            zip_path = Path(pack_agent(str(src), validate=validate))
            cleanup = zip_path if zip_path.with_suffix("").name == src.name else None
        elif src.suffix == ".zip":
            zip_path = src
        else:
            raise ValueError(f"import_agent expects an agent source directory or a .zip, got: {path}")

        params = {"replace": "true"} if replace else None
        try:
            with open(zip_path, "rb") as fh:
                # Straight to fsr-ai (/ai/...): the /api/ai gateway route forwards
                # JSON but drops a multipart body, so fsr-ai answers 422 "file:
                # Field required" (8.0.1).
                resp = self.client.request(
                    "POST",
                    "/ai/agent/import",
                    files={"file": (zip_path.name, fh, "application/zip")},
                    params=params,
                )
        finally:
            # only remove a zip we created next to a source dir, never a caller's file
            if cleanup is not None and cleanup.exists() and src.is_dir():
                cleanup.unlink()
        try:
            return resp.json()
        except ValueError:
            return {}

    def install_agent(
        self,
        path: str,
        *,
        replace: bool = True,
        activate: bool = True,
        allow_upload: bool = True,
        validate: bool = True,
    ) -> AgentRecord:
        """Upload a custom agent and make it usable: allow, import, activate.

        The one-call path for a custom agent (source directory or ``.zip``):

        1. ``allow_upload`` -- turn on *Advanced Development Settings* → AI agents
           (``allow_ai_agent``) if it is off, so the agent is also manageable in
           the UI. The API import itself does not require it.
        2. :meth:`import_agent` -- with ``replace=True`` (the default) an
           installed agent of the same name+version is overwritten in place and
           its new code is live without a service restart.
        3. ``activate`` -- :meth:`activate_agent`, since imports land inactive.

        Returns the installed agent's row from :meth:`list_agents`. Trigger it with
        :meth:`run_agent`; the planner/orchestrator only routes to it when its
        ``tags`` include ``Triage`` (investigation) or ``Insight`` (orchestrator).

        Agent code must call ``self.initialize()`` at the top of ``act()`` (as
        every stock agent does); fsr-ai never calls it, and without it the run
        fails with ``'NoneType' object has no attribute 'get'``.
        """
        if allow_upload and not self.client.system_settings.agent_upload_allowed():
            self.client.system_settings.allow_agent_upload(True)
        resp = self.import_agent(path, replace=replace, validate=validate)
        name, version, uuid = resp.get("name"), resp.get("version"), resp.get("uuid")
        if not (name and uuid):
            raise ValueError(f"agent import returned no name/uuid: {resp!r}")
        if activate:
            self.activate_agent([uuid])
        for agent in self.list_agents():
            if agent.uuid == uuid:
                return agent
        return self.get_agent(name, version or "1.0.0")

    def export_agent(self, agent_id: str | int, dest: str) -> str:
        """Download an installed agent as a ``.zip`` (``POST /api/ai/agent/export/{id}``).

        ``agent_id`` is the agent's numeric ``id``, its uuid or its name (from
        :meth:`list_agents`). The endpoint looks the agent up by numeric id: on
        8.0.1 a uuid there fails with a Postgres ``bigint`` error, so a uuid or
        name is resolved first. Built-in agents export too on 8.0.1 (8.0.0
        refused them, ``CS-AI-AGENT-24``). Writes the archive bytes to ``dest``
        and returns ``dest`` -- handy for cloning a published agent as the
        starting point for a custom one, or for backing up an edited agent
        before re-importing.
        """
        key = str(agent_id).strip()
        if not key:
            raise ValueError("export_agent() requires an agent id, uuid or name")
        if not key.isdigit():
            match = next((a for a in self.list_agents() if key in (a.uuid, a.name)), None)
            if match is None or match.id is None:
                raise ValueError(f"no installed agent {key!r}")
            key = str(match.id)
        resp = self.client.request(
            "POST",
            f"/api/ai/agent/export/{key}",
            headers={"Accept": "application/octet-stream"},
        )
        dest_path = Path(dest)
        dest_path.write_bytes(resp.content)
        return str(dest_path)

    def get_agent_config(self, name: str, version: str) -> AgentConfigDTO:
        """Fetch an agent's configuration (``GET /api/ai/agent/config/{name}/{version}``).

        Returns the ``AiAgentConfigurationDTO`` shape::

            {"agent_name", "agent_version", "name", "default",
             "config": {"config_type", "llm_provider",
                        "mcp_server": [<uuid>, ...], "masking_agent"},
             "config_id"}

        The ``config["mcp_server"]`` list is the per-agent allowlist of MCP
        servers the agent may call. An agent on the *default* config reports
        ``config["config_type"] == "default"``.

        An agent that has never been configured has no config row, and fsr-ai
        answers ``500 Internal server error`` for it (the service returns
        ``None``, which fails the route's response model; source-verified on
        8.0.0, live on 8.0.1). At run time such an agent uses the default
        config, so that is what this returns (``default=True``,
        ``config_type == "default"``); :meth:`update_agent_config` then creates
        the row.
        """
        try:
            resp = self.client.get(f"/api/ai/agent/config/{name}/{version}")
        except APIError as err:
            if err.status_code != 500:
                raise
            self.get_agent(name, version)  # a missing agent still raises
            dto = self.get_default_agent_config()
            dto.agent_name, dto.agent_version, dto.default, dto.config_id = name, version, True, None
            dto.config.config_type = "default"
            return dto
        return AgentConfigDTO.model_validate(resp if isinstance(resp, dict) else {})

    def update_agent_config(
        self,
        agent_name: str,
        agent_version: str,
        config: dict[str, Any] | AgentConfig,
        *,
        name: str | None = None,
        config_id: str | None = None,
    ) -> AgentConfigDTO:
        """Persist an agent's configuration (``POST /api/ai/agent/config``).

        ``config`` is the inner config dict (``llm_provider``, ``mcp_server``,
        ``masking_agent`` …). Prefer the higher-level
        :meth:`allow_mcp_server_for_agent` when all you want is to grant the
        agent one more MCP server.

        Note: this is a ``POST`` even though it updates -- fsr-ai's ``POST
        /config`` handler upserts, and (importantly) the FortiSOAR API gateway
        only authorizes ``POST ^agent/config$`` against ``update.ai_agents``; a
        ``PUT`` matches no ACL rule and is rejected with ``Access Denied``.
        """
        if isinstance(config, AgentConfig):
            config = config.model_dump(exclude_none=True)
        body: dict[str, Any] = {
            "agent_name": agent_name,
            "agent_version": agent_version,
            "config": config,
        }
        if name is not None:
            body["name"] = name
        if config_id is not None:
            body["config_id"] = config_id
        resp = self.client.post("/api/ai/agent/config", data=body)
        return AgentConfigDTO.model_validate(resp if isinstance(resp, dict) else {})

    def get_default_agent_config(self) -> AgentConfigDTO:
        """Fetch the default agent configuration (``GET /api/ai/agent/config/default``).

        This returns the static defaults from fsr-ai's ``app_config.yaml`` (the
        built-in MCP servers), not anything written by
        :meth:`update_default_agent_config` -- the server never reads that back.
        """
        resp = self.client.get("/api/ai/agent/config/default")
        return AgentConfigDTO.model_validate(resp if isinstance(resp, dict) else {})

    def update_default_agent_config(
        self, config: dict[str, Any] | AgentConfig, *, name: str | None = None
    ) -> AgentConfigDTO:
        """Write the default agent configuration (``POST /api/ai/agent/config/default``).

        .. warning::
           This has no effect on any agent. fsr-ai stores the row, but nothing
           reads it back: both :meth:`get_default_agent_config` and the agents at
           run time use the static defaults in ``app_config.yaml`` (the built-in
           MCP servers). Verified in the fsr-ai source on 8.0.0 and 8.0.1.

           To give agents another MCP server, grant it per agent with
           :meth:`allow_mcp_server_for_agent`.

        Emits a :class:`UserWarning` on every call for that reason.
        """
        warnings.warn(
            "update_default_agent_config() is saved but never read by fsr-ai (8.0.0/8.0.1); "
            "agents keep the app_config.yaml defaults. Use allow_mcp_server_for_agent() instead.",
            UserWarning,
            stacklevel=2,
        )
        if isinstance(config, AgentConfig):
            config = config.model_dump(exclude_none=True)
        body: dict[str, Any] = {"config": config, "default": True}
        if name is not None:
            body["name"] = name
        return self.client.post("/api/ai/agent/config/default", data=body)

    def activate_agent(self, uuids: list[str], *, active: bool = True) -> Any:
        """Activate or deactivate agents by uuid (``POST /api/ai/agent/activate``)."""
        return self.client.post("/api/ai/agent/activate", data={"uuids": uuids}, params={"active": active})

    def uninstall_agent(self, name: str, *, force: bool = False) -> None:
        """Delete an installed agent: its record, its config and its files.

        ``DELETE /api/ai/agent/{name}/{version}``. fsr-ai deletes by name and
        answers 200 even when nothing matched, so this looks the agent up first
        and raises :class:`ValueError` if it is not installed. Built-in agents
        (``system``) are refused unless ``force=True``; fsr-ai itself only blocks
        them when its ``block_system_agent_delete`` setting is on.
        """
        agent = next((a for a in self.list_agents() if a.name == name), None)
        if agent is None:
            raise ValueError(f"no installed agent named {name!r}")
        if agent.system and not force:
            raise ValueError(f"{name!r} is a built-in agent; pass force=True to delete it anyway")
        self.client.delete(f"/api/ai/agent/{name}/{agent.version or '1.0.0'}")

    # ------------------------------------------- investigation tool table (8.0.1)
    def _tool_list_record(self) -> Any:
        from ..query import Query

        page = self.client.records(_ORG_CONTEXTS).query(
            Query().eq("category", "INFRA_INFO").eq("subCategory", "TOOL_LIST").limit(1)
        )
        members = list(page)
        return members[0] if members else None

    def investigation_tools(self) -> list[InvestigationTool]:
        """The tool table the investigation planner writes questions from.

        The planner (8.0.1 ``investigation-planning``) writes one question per
        active row, then routes each question to a ``Triage`` agent. It reads the
        table from the Organization Context record ``INFRA_INFO/TOOL_LIST``, or
        uses :data:`DEFAULT_INVESTIGATION_TOOLS` when there is none. A custom
        agent only gets questions when a row names its source; see
        :meth:`set_investigation_tool`.
        """
        record = self._tool_list_record()
        if record is None:
            return list(DEFAULT_INVESTIGATION_TOOLS)
        return InvestigationTool.parse_table(record.get("content") or "")

    def set_investigation_tool(
        self, avenue: str, source: str, required_field: str = "", *, active: bool = True
    ) -> list[InvestigationTool]:
        """Add or replace one row of the planner's tool table (matched by ``avenue``).

        Creates the ``INFRA_INFO/TOOL_LIST`` record from the built-in rows on
        first use, because the record replaces the built-in table rather than
        extending it. Returns the new table. Example, to route questions to a
        custom ``Triage`` agent described as a pentest registry::

            client.ai.set_investigation_tool("Authorized security testing", "Pentest Registry", "host")
        """
        rows = [r for r in self.investigation_tools() if r.avenue.lower() != avenue.lower()]
        rows.append(InvestigationTool(avenue=avenue, source=source, required_field=required_field, active=active))
        self._write_tool_list(rows)
        return rows

    def remove_investigation_tool(self, avenue: str) -> list[InvestigationTool]:
        """Drop a row from the tool table (to stop questions for it, you can also set ``active=False``)."""
        rows = self.investigation_tools()
        kept = [r for r in rows if r.avenue.lower() != avenue.lower()]
        if len(kept) == len(rows):
            raise ValueError(f"no tool-table row with avenue {avenue!r}")
        self._write_tool_list(kept)
        return kept

    def reset_investigation_tools(self) -> None:
        """Delete the ``TOOL_LIST`` record so the planner falls back to its built-in table."""
        record = self._tool_list_record()
        if record is not None:
            self.client.records(_ORG_CONTEXTS).delete(record.uuid)

    def _write_tool_list(self, rows: list[InvestigationTool]) -> None:
        content = InvestigationTool.render_table(rows)
        record = self._tool_list_record()
        records = self.client.records(_ORG_CONTEXTS)
        if record is None:
            records.create(
                {
                    "title": _TOOL_LIST_TITLE,
                    "category": "INFRA_INFO",
                    "subCategory": "TOOL_LIST",
                    "description": "Evidence sources the investigation planner writes questions for.",
                    "content": content,
                    "llmSummarize": False,
                }
            )
        else:
            records.update(record.uuid, {"content": content})

    # -------------------------------------------------- agent ↔ MCP binding
    def mcp_server_names(self) -> dict[str, str]:
        """Return a ``{uuid: name}`` map of every registered MCP server.

        Handy for turning an agent's raw ``mcp_server`` UUID allowlist into
        human-readable names -- see :meth:`list_agent_mcp_servers` with
        ``friendly=True`` and :meth:`describe_agent_mcp_servers`.
        """
        return {
            (m.get("id") or m.get("uuid")): m.get("name")
            for m in self.list_mcp_servers()
            if (m.get("id") or m.get("uuid"))
        }

    def list_agent_mcp_servers(self, name: str, version: str, *, friendly: bool = False) -> list[str]:
        """Return the MCP servers an agent is currently allowed to call.

        By default returns the raw server UUIDs as stored on the agent config.
        Pass ``friendly=True`` to get the registered server *names* instead
        (unknown/unregistered UUIDs are returned unchanged).
        """
        config = self.get_agent_config(name, version).config
        uuids = list(config.mcp_server or [])
        if not friendly:
            return uuids
        names = self.mcp_server_names()
        return [names.get(u, u) for u in uuids]

    def describe_agent_mcp_servers(self, name: str, version: str) -> list[dict[str, str]]:
        """Return the agent's allowed MCP servers as ``[{"uuid", "name"}, ...]``.

        Pairs each allowlisted UUID with its registered name (``name`` falls
        back to the UUID for anything not currently registered).
        """
        config = self.get_agent_config(name, version).config
        uuids = list(config.mcp_server or [])
        names = self.mcp_server_names()
        return [{"uuid": u, "name": names.get(u, u)} for u in uuids]

    def allow_mcp_server_for_agent(self, name: str, version: str, mcp_uuid: str) -> AgentConfigDTO:
        """Grant one agent access to an MCP server (read-modify-write of its config).

        Appends ``mcp_uuid`` to the agent's ``config["mcp_server"]`` allowlist
        (no-op if already present) and PUTs the config back. If the agent is on
        the *default* config it is forked into its own config first, seeded from
        the default, so other agents are unaffected.

        Returns the updated ``AiAgentConfigurationDTO``. Takes effect on the next
        investigation -- no service restart required.
        """
        config, config_name, config_id = self._own_agent_config(name, version)
        allowed = list(config.mcp_server or [])
        if mcp_uuid not in allowed:
            allowed.append(mcp_uuid)
        config.mcp_server = allowed
        return self.update_agent_config(name, version, config, name=config_name, config_id=config_id)

    def _own_agent_config(self, name: str, version: str) -> tuple[AgentConfig, str | None, str | None]:
        """The agent's config to edit, as ``(config, config_name, config_id)``.

        An agent reported as "default" has no row of its own yet: it is seeded
        from the default config so the write creates a dedicated, non-shared row.
        """
        dto = self.get_agent_config(name, version)
        config = dto.config
        config_name, config_id = dto.name, dto.config_id
        if config is None or config.config_type == "default":
            config = self.get_default_agent_config().config
            config.config_type = "custom"
            config.name = config_name = CUSTOM_AGENT_CONFIG_NAME
            # Creating a row needs a client-minted config_id (NOT NULL; the UI
            # generates it). Live-verified on 8.0.1.
            config_id = config_id or str(_uuidlib.uuid4())
        return config, config_name, config_id

    def disallow_mcp_server_for_agent(self, name: str, version: str, mcp_uuid: str) -> AgentConfigDTO:
        """Revoke an agent's access to an MCP server (inverse of
        :meth:`allow_mcp_server_for_agent`)."""
        dto = self.get_agent_config(name, version)
        config = dto.config
        config.mcp_server = [u for u in (config.mcp_server or []) if u != mcp_uuid]
        return self.update_agent_config(name, version, config, name=dto.name, config_id=dto.config_id)

    # ------------------------------------------------- LLM profile switching
    def _llm_profile(self, profile: str) -> LLMConfig:
        found = next((c for c in self.list_llm_configs() if profile in (c.name, c.uuid)), None)
        if found is None:
            raise ValueError(f"no LLM profile {profile!r}")
        return found

    def llm_assignments(self) -> dict[str, dict[str, Any]]:
        """Which LLM profile every agent uses, keyed by agent name.

        Each value is ``{"uuid", "version", "llmconfig", "config_type",
        "llm_provider"}``: ``llmconfig`` is the agent record's profile (set by
        :meth:`assign_llm`'s ``POST /api/ai/agent/llm/config``, what the AI
        Configuration wizard writes), ``llm_provider`` the profile in the
        agent's config row. Save it to put everything back with
        :meth:`restore_llm_assignments`.
        """
        out: dict[str, dict[str, Any]] = {}
        for agent in self.list_agents():
            if not agent.name or not agent.version:
                continue
            cfg = self.get_agent_config(agent.name, agent.version).config
            out[agent.name] = {
                "uuid": agent.uuid,
                "version": agent.version,
                "llmconfig": agent.llmconfig,
                "config_type": cfg.config_type if cfg else None,
                "llm_provider": cfg.llm_provider if cfg else None,
            }
        return out

    def assign_llm(self, profile: str, agents: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """Point agents at an LLM profile (a name or uuid); default: every agent.

        Writes the ``llm_provider`` of each agent's config row (forked from the
        default config if it has none), and the agent record too
        (``POST /api/ai/agent/llm/config``, as the AI Configuration wizard does)
        when the API gateway allows it -- on 8.0.1 it answers 403 to API-key
        and non-UI sessions, so that write is best-effort. Returns the
        assignments from *before* the change, for :meth:`restore_llm_assignments`.
        """
        target = self._llm_profile(profile)
        before = self.llm_assignments()
        chosen = {n: a for n, a in before.items() if agents is None or n in agents}
        missing = sorted(set(agents or []) - set(chosen))
        if missing:
            raise ValueError(f"unknown agent(s): {missing}")
        self._set_agent_records_llm([{"uuid": a["uuid"], "llm": target.uuid} for a in chosen.values()])
        for name, a in chosen.items():
            config, config_name, config_id = self._own_agent_config(name, a["version"])
            config.llm_provider = target.uuid
            self.update_agent_config(name, a["version"], config, name=config_name, config_id=config_id)
        return before

    def _set_agent_records_llm(self, rows: list[dict[str, Any]]) -> bool:
        """``POST /api/ai/agent/llm/config``; False when the gateway refuses it (403)."""
        try:
            self.client.post("/api/ai/agent/llm/config", data=rows)
        except FortiSOARPermissionError:
            return False
        return True

    def restore_llm_assignments(self, snapshot: dict[str, dict[str, Any]]) -> None:
        """Put agents back on the profiles recorded by :meth:`llm_assignments`."""
        current = self.llm_assignments()
        self._set_agent_records_llm(
            [{"uuid": a["uuid"], "llm": a.get("llmconfig")} for n, a in snapshot.items() if n in current]
        )
        for name, a in snapshot.items():
            now = current.get(name)
            if now is None or now["llm_provider"] == a.get("llm_provider") or now["config_type"] == "default":
                continue
            config, config_name, config_id = self._own_agent_config(name, now["version"])
            config.llm_provider = a.get("llm_provider")
            self.update_agent_config(name, now["version"], config, name=config_name, config_id=config_id)

    def setup_connector_llm(
        self,
        profile_name: str,
        *,
        connector: str = "openai",
        model: str,
        api_key: str,
        config_name: str | None = None,
        extra_config: dict[str, Any] | None = None,
        version: str | None = None,
        install: bool = True,
        verify: bool = True,
    ) -> LLMConfig:
        """Add a third-party LLM as a reasoning profile, through its connector.

        The supported path (what the AI Configuration wizard does):

        1. install ``connector`` from Content Hub if it isn't (``install``);
        2. create or update a connector configuration ``config_name`` (default:
           ``profile_name``) holding ``api_key`` and ``model`` -- fsr-ai does not
           send a model name, so **the connector configuration picks the model**
           and each model needs its own configuration;
        3. health-check it and, with ``verify``, run a one-line test completion
           (the health check passes on a valid key whose account has no credits);
        4. create or update the reasoning profile ``profile_name``
           (``provider="fortisoar"``, pointing at that configuration).

        On 8.0.1 ``/api/ai/llm/allowed-providers`` (and the wizard's list) shows
        only ``fortinet-fortiai-proxy`` unless the ``fortiai-configurations``
        key-store record has ``bringYourLLM: {"enabled": true}``, but that only
        affects the UI: fsr-ai calls the ``openai`` connector either way
        (live-verified on 8.0.1).

        Agents keep their current profile; switch them with :meth:`assign_llm`.
        ``model`` is the connector's option label (``"GPT-4.1"`` for
        ``openai``). ``extra_config`` adds connector fields, e.g. Azure OpenAI:
        ``{"api_type": True, "api_base": ..., "api_version": ..., "deployment_id": ...}``.

        Raises:
            LLMSetupError: the install, the health check or the test completion failed.
        """
        installed = [c for c in self.client.content_hub.search_installed_connectors(connector) if c.name == connector]
        if install and not installed:
            hits = [c for c in self.client.content_hub.search_available_connectors(connector) if c.name == connector]
            if not hits:
                raise LLMSetupError(f"connector {connector!r} is not in Content Hub")
            job = self.client.connectors.install(connector, version or hits[0].version, wait=True, timeout=600)
            status = getattr(job, "status", None) or (job.get("status") if isinstance(job, dict) else None)
            if status != "Import Complete":
                raise LLMSetupError(f"installing {connector}: {status}")
        name = config_name or profile_name
        cfg = self.client.connectors.upsert_configuration(
            connector, {"apiKey": api_key, "model": model, **(extra_config or {})}, name=name, version=version
        )
        health = self.client.connectors.healthcheck(connector, config=name)
        if health.status != "Available":
            raise LLMSetupError(
                f"{connector} configuration {name!r} health check: {health.status} {health.message or ''}"
            )
        config_id = getattr(cfg, "config_id", None) or getattr(cfg, "id", None)
        if verify:
            # the health check only validates the key; a one-line completion
            # also catches an exhausted quota or a model the account can't use
            try:
                result = self.client.connectors.execute(
                    connector,
                    "agent_chat_completions",
                    config=config_id,
                    params={"messages": [{"role": "user", "content": "Reply with OK."}]},
                )
            except APIError as exc:
                raise LLMSetupError(f"{connector} test completion failed: {exc}") from exc
            status = str(getattr(result, "status", "") or "")
            if status and status.lower() not in ("success", "finished"):
                raise LLMSetupError(f"{connector} test completion: {status} {getattr(result, 'message', '') or ''}")
        return self.upsert_llm_config(
            profile_name,
            provider="fortisoar",
            modelname=model,
            config={"connector_name": connector, "connector_config_id": config_id},
        )

    # -------------------------------------------------- tool-usage evidence
    def tool_usage(
        self,
        *,
        correlation_id: str | None = None,
        limit: int = 500,
    ) -> list[ToolCall]:
        """Return the tool calls the LLM made, from the ``llm_activity_logs``.

        Every reasoning step is logged to the ``llm_activity_logs`` module with a
        structured ``response`` of ``{"content", "tool_name", "tool_args"}``. When
        the model selects a tool, ``tool_name`` is populated -- *this* is the
        deterministic record of which MCP/connector tool ran (the prompt text does
        **not** carry it). This returns one entry per tool-selecting log::

            {"tool_name", "tool_args", "correlation_id", "title", "model", ...}

        Args:
            correlation_id: scope to a single investigation. **The investigation's
                ``task_id`` (from :meth:`investigate_alert`) IS this
                ``correlationID``** -- every log for that run is stamped with it --
                so pass a ``task_id`` here to see exactly what that run called.
            limit: max log records to scan when ``correlation_id`` is omitted
                (the appliance returns newest first).

        See :meth:`investigation_tool_calls` for the per-investigation shortcut.

        .. note::
           8.0.1 writes no ``llm_activity_logs`` for alert investigations, so this
           returns ``[]`` for them. :meth:`investigation_tool_calls` reads the
           traces there instead.
        """
        params: dict[str, Any] = {"$limit": limit}
        if correlation_id:
            params["correlationID"] = correlation_id
        resp = self.client.get("/api/3/llm_activity_logs", params=params)
        records = extract_members(resp)
        calls: list[ToolCall] = []
        for rec in records or []:
            response = rec.get("response")
            if isinstance(response, str):
                try:
                    response = json.loads(response)
                except (ValueError, TypeError):
                    response = {}
            if not isinstance(response, dict):
                continue
            tool_name = response.get("tool_name")
            if not tool_name:
                continue
            calls.append(
                ToolCall(
                    tool_name=tool_name,
                    tool_args=response.get("tool_args"),
                    correlation_id=rec.get("correlationID"),
                    title=rec.get("title"),
                    model=rec.get("modelName"),
                    latency_ms=rec.get("latencyMs"),
                )
            )
        return calls

    def find_investigations(self, alert: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """Recover the ``task_id``\\ s of **all** past investigations of an alert.

        The alert's ``triagetaskid`` field (see
        :meth:`get_investigation_for_alert`) only keeps the *latest* run, so to
        find earlier ones -- an alert investigated repeatedly yields several -- this
        searches the ``llm_activity_logs`` instead: every log for a run embeds the
        alert's payload, so a full-text search for the alert uuid surfaces them,
        and their distinct ``correlationID``\\ s are exactly the investigations'
        ``task_id``\\ s. Use :meth:`get_investigation_for_alert` for the cheap
        single-field lookup when you only need the current one.

        Args:
            alert: an alert uuid or record reference (``"alerts:<uuid>"`` / IRI).
            limit: max log records to search (newest first).

        Returns:
            ``[{"task_id", "log_count"}, ...]``, one per distinct investigation,
            ordered by most-recently-seen first. On 8.0.1, which writes no
            ``llm_activity_logs`` for investigations, the rows come from the
            tracer store instead: ``{"task_id", "log_count": 0, "source":
            "traces", "status", "start_time"}``, newest first. Feed a ``task_id`` to
            :meth:`investigation_tool_calls` to see what that run invoked.
        """
        uuid = _uuid_from_ref(alert)
        resp = self.client.get("/api/3/llm_activity_logs", params={"$search": uuid, "$limit": limit})
        records = extract_members(resp)
        counts: dict[str, int] = {}
        for rec in records or []:
            cid = rec.get("correlationID")
            if cid:
                counts[cid] = counts.get(cid, 0) + 1
        if counts:
            return [{"task_id": cid, "log_count": n} for cid, n in counts.items()]
        return self._find_investigations_in_traces(uuid, limit=min(limit, 500))

    def _find_investigations_in_traces(self, alert_uuid: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """8.0.1: investigations of an alert, from the tracer store.

        Each ``Alert Investigation`` trace's root span input carries the alert
        record, so the alert uuid appears in it. One ``span`` request per
        candidate trace. Only the newest ``limit`` (max 500) traces are
        reachable -- the trace-list cursor fails server-side on 8.0.1 (see
        :meth:`AITracesAPI.iter`).
        """
        try:
            traces = self.traces.list(limit=limit)
        except APIError:  # 8.0.0: no tracer store
            return []
        found: list[dict[str, Any]] = []
        for t in traces:
            if t.name != INVESTIGATION_TRACE_NAME or t.parent_trace_id or not t.root_span_id:
                continue
            try:
                root = self.traces.span(t.root_span_id)
            except APIError:
                continue
            if alert_uuid in json.dumps(root.input, default=str):
                found.append(
                    {
                        "task_id": t.trace_id,
                        "log_count": 0,
                        "source": "traces",
                        "status": t.status,
                        "start_time": t.start_time,
                    }
                )
        return found

    def investigation_tool_calls(self, task_id: str) -> list[ToolCall]:
        """The tool calls made during one investigation (by its ``task_id``).

        8.0.1+: read from the investigation's traces
        (:meth:`AITracesAPI.investigation`) -- every call the agents ran, each
        with its MCP ``server``, ``agent``, ``question``, ``selected_by``,
        ``output`` and ``error`` (``source == "traces"``). 8.0.1 no longer writes
        ``llm_activity_logs`` for investigations, so the log path alone returns
        nothing there.

        8.0.0 (no tracer store, or no trace for this ``task_id``): falls back to
        ``tool_usage(correlation_id=task_id)`` -- the triage ``task_id`` is the
        ``correlationID`` on that run's ``llm_activity_logs``, which records only
        the LLM-selected calls.
        """
        traced = self._traced_tool_calls(task_id)
        return traced if traced is not None else self.tool_usage(correlation_id=task_id)

    def _traced_tool_calls(self, task_id: str) -> list[ToolCall] | None:
        """The investigation's calls from its traces, or ``None`` when there is no trace."""
        try:
            inv = self.traces.investigation(task_id)
        except APIError:  # 8.0.0 (no /ai/traces) or an unknown trace id
            return None
        if not inv.runs or not any(r.agent for r in inv.runs):
            return None
        return [
            ToolCall(
                tool_name=c.tool_name,
                tool_args=c.args,
                correlation_id=task_id,
                title=c.agent,
                server=c.server,
                server_uuid=c.server_id,
                source="traces",
                agent=c.agent,
                question=c.question,
                selected_by=c.selected_by,
                output=c.output,
                error=c.error,
                cached=c.cached,
                span_id=c.span_id,
            )
            for c in inv.tool_calls
        ]

    # ----------------------------------------------------------- internals
    def _fetch_alert(self, ref: str) -> dict[str, Any]:
        """Resolve a record reference to the full alert JSON for triage."""
        return self.client.alerts.get(_uuid_from_ref(ref))

    @staticmethod
    def _alert_uuid(alert: dict[str, Any]) -> str | None:
        """Best-effort extraction of an alert's uuid from its record dict."""
        if not isinstance(alert, dict):
            return None
        ref = alert.get("uuid") or alert.get("@id") or alert.get("id")
        return _uuid_from_ref(str(ref)) if ref else None


def _uuid_from_ref(ref: str) -> str:
    """Strip a record reference (``alerts:<uuid>`` / IRI / bare uuid) to its uuid."""
    return ref.rstrip("/").split("/")[-1].split(":")[-1]


def _as_list(resp: Any) -> list[dict[str, Any]]:
    """Coerce a FortiAI response into a list (handles bare lists + Hydra)."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        return resp.get("hydra:member") or resp.get("data") or [resp]
    return []
