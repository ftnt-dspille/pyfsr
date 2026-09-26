"""Evaluation suite model: stub MCP servers with planted ground truth, and the
scenarios (alert + expected tool use + facts + verdict) scored against them.

See ``suites/default.yaml`` for a worked suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_SUITE = Path(__file__).with_name("suites") / "default.yaml"


def _alternatives(pattern: str) -> list[str]:
    return [p.strip().lower() for p in str(pattern).split("|") if p.strip()]


def text_matches(pattern: str, text: str) -> bool:
    """True when ``text`` (lower-cased) contains any ``|``-separated alternative."""
    low = text.lower()
    return any(alt in low for alt in _alternatives(pattern))


def _arg_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)


class SuiteModel(BaseModel):
    """Base for suite models: unknown keys are an error (catches YAML typos)."""

    model_config = ConfigDict(extra="forbid")


class ToolRule(SuiteModel):
    """``match`` maps an argument name (or ``*`` for all) to ``a|b`` substrings."""

    match: dict[str, str] = Field(default_factory=dict)
    result: Any = None


class StubTool(SuiteModel):
    name: str
    description: str = ""
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    rules: list[ToolRule] = Field(default_factory=list)
    default: Any = Field(default_factory=lambda: {"results": []})

    def answer(self, args: dict[str, Any] | None) -> tuple[Any, int | None]:
        """What the stub listener returns for ``args``: ``(result, rule_index)``.

        ``rule_index`` is ``None`` when no rule matched (the default answer) --
        the agent asked about an entity this scenario has no data for. Mirrors
        ``listener.answer`` so scoring needs no call log.
        """
        args = args if isinstance(args, dict) else {}
        for index, rule in enumerate(self.rules):
            if all(
                text_matches(pattern, json.dumps(args, default=str) if key == "*" else _arg_text(args.get(key, "")))
                for key, pattern in rule.match.items()
            ):
                return rule.result, index
        return self.default, None


class StubServer(SuiteModel):
    """One stub MCP server, registered in FortiSOAR under ``name``."""

    name: str
    instructions: str = ""
    role: Literal["evidence", "distractor"] = "evidence"
    #: ``fsr`` wraps results in fsr-ai's ``{"status", "result", "error"}`` envelope
    #: (what the built-in servers return); ``raw`` sends the bare JSON, as most
    #: third-party MCP servers do -- on 8.0.1 some agents crash on that.
    envelope: Literal["fsr", "raw"] = "fsr"
    #: provider agents (by agent *name*) allowed to use this server
    agents: list[str] = Field(default_factory=list)
    tools: list[StubTool]


class ExpectedCall(SuiteModel):
    server: str
    tool: str
    #: ``a|b`` substrings; the call counts as the *right query* when its args contain one
    args: str = ""
    #: the entity is only learned from another tool's result (a pivot)
    chain: bool = False
    required: bool = True


class Fact(SuiteModel):
    id: str
    #: ``a|b`` substrings that show the fact was surfaced
    match: str
    #: server key whose answer carries the fact
    source: str
    chain: bool = False
    required: bool = True
    what: str = ""


class Verdict(SuiteModel):
    expected: list[str]
    partial: list[str] = Field(default_factory=list)

    def score(self, classification: str | None) -> float:
        got = (classification or "").strip().lower()
        if not got:
            return 0.0
        if any(v.lower() == got for v in self.expected):
            return 1.0
        if any(v.lower() == got for v in self.partial):
            return 0.5
        return 0.0


class Scenario(SuiteModel):
    id: str
    title: str = ""
    kind: str = ""
    alert: dict[str, Any]
    verdict: Verdict
    expected_calls: list[ExpectedCall] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)

    def alert_text(self) -> str:
        return json.dumps(self.alert, default=str).lower()


class Suite(SuiteModel):
    servers: dict[str, StubServer]
    scenarios: list[Scenario]

    @model_validator(mode="after")
    def _check(self) -> Suite:
        problems = []
        for sc in self.scenarios:
            for call in sc.expected_calls:
                srv = self.servers.get(call.server)
                if srv is None:
                    problems.append(f"{sc.id}: expected call names unknown server {call.server!r}")
                elif call.tool not in {t.name for t in srv.tools}:
                    problems.append(f"{sc.id}: {call.server} has no tool {call.tool!r}")
            for fact in sc.facts:
                if fact.source not in self.servers:
                    problems.append(f"{sc.id}: fact {fact.id} names unknown server {fact.source!r}")
                # a fact already in the alert would be "recalled" without any tool use
                if text_matches(fact.match, sc.alert_text()):
                    problems.append(f"{sc.id}: fact {fact.id} ({fact.match!r}) already appears in the alert")
        names = [s.name for s in self.servers.values()]
        if len(names) != len(set(names)):
            problems.append("server names must be unique (they are the MCP registration names)")
        tools = [t.name for s in self.servers.values() for t in s.tools]
        dupes = sorted({t for t in tools if tools.count(t) > 1})
        if dupes:
            problems.append(f"tool names must be unique across servers: {dupes}")
        if problems:
            raise ValueError("invalid evaluation suite:\n  - " + "\n  - ".join(problems))
        return self

    def scenario(self, scenario_id: str) -> Scenario:
        for sc in self.scenarios:
            if sc.id == scenario_id:
                return sc
        raise KeyError(f"no scenario {scenario_id!r} (have {[s.id for s in self.scenarios]})")

    def server_key(self, registered_name: str | None) -> str | None:
        """Map a trace's ``mcp_server_name`` back to the suite key."""
        for key, srv in self.servers.items():
            if srv.name == registered_name:
                return key
        return None

    def tool_server(self, tool_name: str) -> str | None:
        for key, srv in self.servers.items():
            if any(t.name == tool_name for t in srv.tools):
                return key
        return None

    def fixtures(self) -> dict[str, Any]:
        """The document the stub listener serves (``load_fixtures`` op)."""
        return {
            "servers": {
                key: {
                    "name": srv.name,
                    "instructions": srv.instructions,
                    "envelope": srv.envelope,
                    "tools": [t.model_dump() for t in srv.tools],
                }
                for key, srv in self.servers.items()
            }
        }


def load_suite(path: str | Path | None = None) -> Suite:
    """Load a suite YAML (default: the bundled ``suites/default.yaml``)."""
    with open(path or DEFAULT_SUITE, encoding="utf-8") as f:
        return Suite.model_validate(yaml.safe_load(f))
