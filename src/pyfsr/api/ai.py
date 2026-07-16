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
install an agent package (zip)                     ``POST /api/ai/agent/import``
export an installed agent as a zip                 ``POST /api/ai/agent/export/{agent_id}``
get an agent's configuration                      ``GET  /api/ai/agent/config/{name}/{version}``
update an agent's configuration                   ``POST /api/ai/agent/config``
get/update the default agent configuration        ``GET/POST /api/ai/agent/config/default``
activate/deactivate agents                        ``POST /api/ai/agent/activate``
which connectors can be hosted as an MCP server    ``GET  /mcp/servers/connector``
host a connector as an MCP server                  ``POST /mcp/add/tools`` (+ ``mcp_configurations``)
change a hosted connector server's exposed tools   ``PUT  /mcp/tools/{uuid}``
list a hosted server's current tools               ``POST /mcp/config/export``
remove specific tools from a hosted server         ``DELETE /mcp/tools/delete``
================================================  ==================================================

Connector → MCP server
-----------------------
Any installed connector can be *hosted* as an MCP server — each operation
becomes a tool — via :meth:`AIApi.host_connector_as_mcp_server`. This is
distinct from :meth:`AIApi.register_mcp_server` (connecting to an *external*
MCP server); both land in the same ``mcp_configurations`` collection,
distinguished by ``type`` (``"connector"`` vs ``"internal"``/``"external"``).
Check :meth:`AIApi.mcp_connector_candidates` first — some connectors can't be
hosted this way. These ``/mcp/...`` routes (unlike everything else here) live
at the appliance root rather than under ``/api/3``.

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
import zipfile
from pathlib import Path
from typing import Any

from ..models._ai import (
    AgentConfig,
    AgentConfigDTO,
    AgentRecord,
    ConnectorMcpCandidates,
    InvestigationHandle,
    InvestigationQuestion,
    InvestigationResult,
    LLMConfig,
    LLMProvider,
    MCPServerConfig,
    MCPServerRef,
    MCPValidateResult,
    ToolCall,
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

    ``source_dir`` is the package root — the folder that *is* the agent (holds
    ``info.json``, ``agent.py``, ``prompt.yaml``, ``config/memory.yaml``,
    ``images/``). The archive is written with that folder as its single
    top-level entry (``<name>/info.json`` …), which is the layout
    :meth:`AIApi.import_agent` expects.

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
            # arcname keeps <name>/ as the top-level folder inside the zip
            zf.write(path, arcname=str(Path(root.name) / path.relative_to(root)))
    return str(out_path)


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
    def start_alert_investigation(self, alert: dict[str, Any] | str, *, link: bool = True) -> InvestigationHandle:
        """Kick off an asynchronous AI investigation of one alert.

        ``alert`` may be the full alert record (a dict, as returned by
        ``client.alerts.get(...)``) or a record reference (``"<uuid>"``,
        ``"alerts:<uuid>"`` or a full ``/api/3/alerts/<uuid>`` IRI), in which
        case the record is fetched first. The whole alert JSON is posted to the
        triage pipeline.

        When ``link`` is true (default), the returned ``task_id`` is written back
        to the alert's :data:`ALERT_TRIAGE_TASK_KEY` (``triagetaskid``) field,
        exactly as the FortiSOAR UI does — this is what makes
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

        Reads the alert's :data:`ALERT_TRIAGE_TASK_KEY` (``triagetaskid``) field —
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
        it ends on a terminal status — ``"completed"`` or ``"failed"`` (see
        :data:`TERMINAL_STATUSES`). Returns ``""`` if the status can't be read.
        """
        resp = self.client.get(f"/api/ai/agents/{task_id}/status")
        return (resp or {}).get("status", "") if isinstance(resp, dict) else ""

    def get_result(self, task_id: str) -> InvestigationResult:
        """Fetch the full investigation result/verdict for a triage task.

        The payload carries the per-stage ``phases`` (normalization →
        hypothesis → planning → evidence → verdict), any ``logs``, and — once
        ``status == "completed"`` — the synthesized verdict/summary. While the
        pipeline is still running this returns the partial progress so far.
        """
        resp = self.client.get(f"/api/ai/agents/{task_id}/result")
        return InvestigationResult.model_validate(resp if isinstance(resp, dict) else {"result": resp})

    def investigation_questions(self, task_id: str) -> list[InvestigationQuestion]:
        """Return the investigation's question-by-question evidence.

        This is the data behind the UI's *Investigation Questions* panel — one
        entry per question the agents asked, shaped as::

            {"index", "question", "agent", "input", "response", "evidence",
             "supports": [hyp_id, ...], "weakens": [hyp_id, ...],
             "information_type", "status"}

        ``input`` is the agent's tool input (``params``), ``response`` its answer
        (``result``), and ``evidence`` the natural-language justification derived
        from the tool output. ``supports``/``weakens`` are the hypothesis ids this
        answer votes for/against — the link into the weighting (see
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
        hypothesis's resolved status and the final verdict — i.e. proof that the
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

    def run_agent(self, agent_name: str, data: dict[str, Any]) -> InvestigationHandle:
        """Trigger a single named agent (e.g. ``"ioc-enrichment"``) directly.

        Returns the ``{"task_id", "status"}`` handle; poll with
        :meth:`get_status` / :meth:`get_result` exactly like an investigation.
        """
        resp = self.client.post(f"/api/ai/triage/{agent_name}/trigger", data=data)
        return InvestigationHandle.model_validate(resp if isinstance(resp, dict) else {})

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
    def list_mcp_servers(self) -> list[MCPServerRef]:
        """List registered MCP servers (id + name) the AI agents can be granted."""
        return [MCPServerRef.model_validate(m) for m in _as_list(self.client.get("/api/ai/mcp"))]

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

        Thin convenience over :meth:`validate_mcp_server` — opens the connection,
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
        # ``_as_list`` coerces a bare dict to ``[resp]`` — which would turn an empty
        # response into a phantom member and mask a genuine "not found".
        members = extract_members(self.client.get("/api/3/mcp_configurations", params={"name": name_or_uuid}))
        for record in members:
            if isinstance(record, dict):
                return MCPServerConfig.model_validate(record)
        raise ValueError(f"MCP configuration {name_or_uuid!r} not found")

    def mcp_tool_catalog(self) -> dict[str, dict[str, Any]]:
        """Map every advertised tool to the MCP server that owns it.

        Probes *each* registered MCP server's ``tools/list`` (via its stored
        config, decoding the ``authentication`` JSON string) and returns::

            {"<tool_name>": {"server", "server_uuid", "description"}, ...}

        This is the **vendor-neutral** tool→server attribution: it works for any
        registered server (FortiSIEM, a 3rd-party SIEM, internal FSR servers, …),
        not just FortiSIEM, and needs no extra credentials when the stored token
        is still valid. Servers that fail to probe (e.g. an expired bearer token)
        are skipped — mint a fresh token and :meth:`update_mcp_server` first to
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

        Returns the :meth:`tool_usage` dicts (``tool_name``, ``tool_args``,
        ``correlation_id``, …) each extended with ``server`` and ``server_uuid``.
        """
        if catalog is None:
            catalog = self.mcp_tool_catalog()
        out: list[ToolCall] = []
        for call in self.investigation_tool_calls(task_id):
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
        resp = self.client.post("/api/3/mcp_configurations", data=config)
        return MCPServerConfig.model_validate(resp if isinstance(resp, dict) else {})

    def update_mcp_server(self, uuid: str, config: dict[str, Any]) -> MCPServerConfig:
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
        resp = self.client.put(f"/api/3/mcp_configurations/{uuid}", data=config)
        return MCPServerConfig.model_validate(resp if isinstance(resp, dict) else {})

    def save_mcp_server(self, config: dict[str, Any], *, validate: bool = True) -> MCPServerConfig:
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
            if not result.valid:
                raise ValueError(f"MCP server did not validate, not saving: {result.message or result}")
        uuid = config.get("uuid")
        if uuid:
            return self.update_mcp_server(uuid, config)
        return self.register_mcp_server(config)

    def upsert_mcp_server(self, config: dict[str, Any], *, validate: bool = True) -> MCPServerConfig:
        """Create or update an MCP server **keyed by name** — re-runnable setup.

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
        """Validate, register, and learn the tool list — the one-liner for
        the "validate → check tools → upsert → print uuid" every MCP setup
        script hand-rolls today (this repo's ``fortisiem_mcp_setup_and_test.py``
        and ``register_and_call_public_mcp_server.py`` examples included).

        Raises ``ValueError`` on a failed validation (same guarantee
        :meth:`upsert_mcp_server`'s default ``validate=True`` already gives —
        this just avoids the second round-trip callers were already making to
        also learn the tool list, by reusing the one validation response for
        both).

        Returns the upserted record (with its ``uuid``) plus a ``tools`` key
        — the tool list the validation probe reported, so callers don't need
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
        """Convert an installed connector into a hosted MCP server — the exact
        flow the FortiSOAR UI's "Add MCP Server → Connector" wizard runs.

        Each of the connector's operations becomes an MCP tool. By default all
        (enabled) operations are exposed; pass ``operations`` (a list of
        operation names, e.g. ``["fetch_email_new"]``) to expose a subset —
        see :meth:`~pyfsr.api.connectors.ConnectorsAPI.operations` to list them.

        If the connector requires configuration (most do), the connector's
        default configuration is used unless ``config_id`` names a specific one
        (see :meth:`~pyfsr.api.connectors.ConnectorsAPI.configurations`). A
        connector with no configured instance yet is still hosted (mirroring
        the UI), but its tools won't work until one is configured.

        Check :meth:`mcp_connector_candidates` first — some connectors
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

        ``operations`` is the *full* desired set of exposed operation names —
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

        Optional keyword filters are passed straight through as query params —
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
        defect. Run this before :meth:`import_agent` when authoring — it catches
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

        ``POST /api/ai/agent/import`` (multipart ``file``). ``path`` may be either
        an already-built ``.zip`` or an agent **source directory** — a directory
        is packed on the fly with :func:`pack_agent` (which validates it first).
        Pass ``replace=True`` to overwrite an already-installed agent of the same
        name+version (``?replace=true``); without it, re-importing an existing
        name+version is rejected by the service.

        The uploaded agent lands **inactive** — call :meth:`activate_agent` with
        its uuid (from the response or :meth:`list_agents`) to make the
        orchestrator eligible to route to it, and give it an LLM/MCP config via
        :meth:`update_agent_config` if it isn't using the default.

        Set ``validate=False`` to skip local package validation (e.g. to upload a
        vendor ``.zip`` you don't want re-inspected). Ignored when ``path`` is a
        ``.zip`` — only source directories are validated/packed.

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
                resp = self.client.request(
                    "POST",
                    "/api/ai/agent/import",
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

    def export_agent(self, agent_id: str, dest: str) -> str:
        """Download an installed agent as a ``.zip`` (``POST /api/ai/agent/export/{agent_id}``).

        ``agent_id`` is the agent's uuid (from :meth:`list_agents`). Writes the
        archive bytes to ``dest`` and returns ``dest`` — handy for cloning a
        published agent as the starting point for a custom one, or for backing up
        an edited agent before re-importing.
        """
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("export_agent() requires a non-empty agent uuid")
        resp = self.client.request(
            "POST",
            f"/api/ai/agent/export/{agent_id.strip()}",
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
        """
        resp = self.client.get(f"/api/ai/agent/config/{name}/{version}")
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

        Note: this is a ``POST`` even though it updates — fsr-ai's ``POST
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
        """Fetch the default agent configuration (``GET /api/ai/agent/config/default``)."""
        resp = self.client.get("/api/ai/agent/config/default")
        return AgentConfigDTO.model_validate(resp if isinstance(resp, dict) else {})

    def update_default_agent_config(
        self, config: dict[str, Any] | AgentConfig, *, name: str | None = None
    ) -> AgentConfigDTO:
        """Update the default agent configuration (``POST /api/ai/agent/config/default``).

        Agents left on the default config inherit this ``mcp_server`` list, so
        appending a uuid here grants the server to *every* such agent at once.
        """
        if isinstance(config, AgentConfig):
            config = config.model_dump(exclude_none=True)
        body: dict[str, Any] = {"config": config, "default": True}
        if name is not None:
            body["name"] = name
        return self.client.post("/api/ai/agent/config/default", data=body)

    def activate_agent(self, uuids: list[str], *, active: bool = True) -> Any:
        """Activate or deactivate agents by uuid (``POST /api/ai/agent/activate``)."""
        return self.client.post("/api/ai/agent/activate", data={"uuids": uuids}, params={"active": active})

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
        investigation — no service restart required.
        """
        dto = self.get_agent_config(name, version)
        config = dto.config
        # An agent reported as "default" has no row of its own yet — seed from
        # the default config so the write creates a dedicated, non-shared row.
        if config.config_type == "default" or config is None:
            config = self.get_default_agent_config().config
            config.config_type = None
        allowed = list(config.mcp_server or [])
        if mcp_uuid not in allowed:
            allowed.append(mcp_uuid)
        config.mcp_server = allowed
        return self.update_agent_config(name, version, config, name=dto.name, config_id=dto.config_id)

    def disallow_mcp_server_for_agent(self, name: str, version: str, mcp_uuid: str) -> AgentConfigDTO:
        """Revoke an agent's access to an MCP server (inverse of
        :meth:`allow_mcp_server_for_agent`)."""
        dto = self.get_agent_config(name, version)
        config = dto.config
        config.mcp_server = [u for u in (config.mcp_server or []) if u != mcp_uuid]
        return self.update_agent_config(name, version, config, name=dto.name, config_id=dto.config_id)

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
        the model selects a tool, ``tool_name`` is populated — *this* is the
        deterministic record of which MCP/connector tool ran (the prompt text does
        **not** carry it). This returns one entry per tool-selecting log::

            {"tool_name", "tool_args", "correlation_id", "title", "model", ...}

        Args:
            correlation_id: scope to a single investigation. **The investigation's
                ``task_id`` (from :meth:`investigate_alert`) IS this
                ``correlationID``** — every log for that run is stamped with it —
                so pass a ``task_id`` here to see exactly what that run called.
            limit: max log records to scan when ``correlation_id`` is omitted
                (the appliance returns newest first).

        See :meth:`investigation_tool_calls` for the per-investigation shortcut.
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
        find earlier ones — an alert investigated repeatedly yields several — this
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
            ordered by most-recently-seen first. Feed a ``task_id`` to
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
        return [{"task_id": cid, "log_count": n} for cid, n in counts.items()]

    def investigation_tool_calls(self, task_id: str) -> list[ToolCall]:
        """The tool calls made during one investigation (by its ``task_id``).

        Shorthand for ``tool_usage(correlation_id=task_id)`` — the triage
        ``task_id`` returned by :meth:`investigate_alert` is the ``correlationID``
        on that run's ``llm_activity_logs``. Pair with :meth:`list_mcp_tools` to
        confirm a specific server's tool (e.g. a FortiSIEM tool) was actually
        used while investigating a given alert.
        """
        return self.tool_usage(correlation_id=task_id)

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
