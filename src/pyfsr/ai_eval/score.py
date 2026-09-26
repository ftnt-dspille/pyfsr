"""Score one investigation against a scenario's ground truth, and aggregate runs.

Every number comes from the investigation's 8.0.1 trace
(:meth:`pyfsr.api.ai.AITracesAPI.investigation`) plus its result summary; the
stub servers' fixed answers make it deterministic which calls *could* have
surfaced which facts.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from ..models import InvestigationTrace, TracedToolCall
from .scenario import Scenario, Suite, text_matches

#: Composite-score weights. Verdict and evidence use dominate; tool choice,
#: query accuracy, pivoting and avoiding waste make up the rest.
WEIGHTS = {
    "verdict": 0.30,
    "fact_use": 0.25,
    "tool_recall": 0.15,
    "query_accuracy": 0.10,
    "chain_recall": 0.10,
    "precision": 0.10,
}


class TokenPricing(BaseModel):
    """What an investigation's tokens cost.

    Defaults are FortiAI's: 5,000,000 tokens a month included per appliance,
    top-ups at $100 per 500,000 tokens (``usd_per_million=200``). FortiAI meters
    input and output tokens alike. For a native provider profile (your own
    OpenAI/Anthropic/Gemini key) set ``usd_per_million`` to a blended rate, or
    ``input_usd_per_million``/``output_usd_per_million`` to price the two apart.
    """

    usd_per_million: float = 200.0
    input_usd_per_million: float | None = None
    output_usd_per_million: float | None = None
    free_tokens_per_month: int = 5_000_000

    def cost(self, input_tokens: int, output_tokens: int, billed_tokens: int | None = None) -> float:
        """USD for one investigation; ``billed_tokens`` (the metered count) wins when given."""
        if billed_tokens is not None:
            return round(billed_tokens * self.usd_per_million / 1e6, 4)
        rate_in = self.usd_per_million if self.input_usd_per_million is None else self.input_usd_per_million
        rate_out = self.usd_per_million if self.output_usd_per_million is None else self.output_usd_per_million
        return round((input_tokens * rate_in + output_tokens * rate_out) / 1e6, 4)


DEFAULT_PRICING = TokenPricing()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _args(call: TracedToolCall) -> dict[str, Any]:
    args = call.args
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return {"_raw": args}
    return args if isinstance(args, dict) else {}


def _ratio(hit: int, total: int) -> float | None:
    return round(hit / total, 3) if total else None


class ScoredCall(BaseModel):
    server: str | None
    tool: str | None
    args: dict[str, Any] = Field(default_factory=dict)
    selected_by: str = "code"
    agent: str | None = None
    #: rule index that answered (None = default answer: an entity with no data)
    rule: int | None = None
    distractor: bool = False
    failed: bool = False


class ExpectedCallResult(BaseModel):
    server: str
    tool: str
    args: str
    chain: bool
    required: bool
    called: bool
    right_query: bool
    selected_by: str | None = None


class FactResult(BaseModel):
    id: str
    what: str
    chain: bool
    required: bool
    retrieved: bool  # some tool call returned it
    in_answers: bool  # an agent's answer/evidence mentions it
    in_summary: bool  # the final summary/verdict mentions it


class RunScore(BaseModel):
    scenario: str
    task_id: str | None = None
    status: str | None = None
    classification: str | None = None
    verdict: float = 0.0
    tool_recall: float | None = None
    query_accuracy: float | None = None
    chain_recall: float | None = None
    precision: float | None = None
    fact_retrieval: float | None = None
    fact_use: float | None = None
    fact_in_summary: float | None = None
    composite: float = 0.0
    eval_calls: int = 0
    wasted_calls: int = 0
    distractor_calls: int = 0
    failed_calls: int = 0
    lost_facts: list[str] = Field(default_factory=list)
    expected: list[ExpectedCallResult] = Field(default_factory=list)
    facts: list[FactResult] = Field(default_factory=list)
    calls: list[ScoredCall] = Field(default_factory=list)
    trace_metrics: dict[str, Any] = Field(default_factory=dict)
    duration_s: float | None = None
    error: str | None = None
    #: token totals over every run of the investigation (from the traces)
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    llm_calls: int | None = None
    #: tokens FortiAI deducted from the balance during the run (None if not measured)
    metered_tokens: int | None = None
    cost_usd: float | None = None


def score_run(
    suite: Suite,
    scenario: Scenario,
    inv: InvestigationTrace | None,
    *,
    summary: Any = None,
    classification: str | None = None,
    duration_s: float | None = None,
    metered_tokens: int | None = None,
    pricing: TokenPricing = DEFAULT_PRICING,
) -> RunScore:
    """Score one finished investigation.

    ``summary`` is the result's summary payload (anything JSON-able; searched
    for facts); ``classification`` the verdict label (e.g. ``"Malicious"``).
    ``metered_tokens`` is the FortiAI balance drop over the run, when measured;
    cost is priced from it, else from the traced tokens.
    """
    score = RunScore(
        scenario=scenario.id, classification=classification, duration_s=duration_s, metered_tokens=metered_tokens
    )
    score.verdict = scenario.verdict.score(classification)
    if inv is None:
        score.error = "no trace"
        score.composite = round(WEIGHTS["verdict"] * score.verdict, 3)
        if metered_tokens is not None:
            score.cost_usd = pricing.cost(0, 0, metered_tokens)
        return score
    score.task_id, score.status = inv.task_id, inv.status
    score.trace_metrics = inv.metrics()
    tokens = inv.tokens or {}
    score.input_tokens = int(tokens.get("input_tokens") or 0)
    score.output_tokens = int(tokens.get("output_tokens") or 0)
    score.total_tokens = int(tokens.get("total_tokens") or 0)
    score.llm_calls = int(tokens.get("llm_calls") or 0)
    score.cost_usd = pricing.cost(score.input_tokens, score.output_tokens, metered_tokens)

    # -- the calls that reached our stub servers
    calls: list[ScoredCall] = []
    outputs: list[str] = []
    for c in inv.tool_calls:
        key = suite.server_key(c.server) or suite.tool_server(c.tool_name or "")
        if key is None:
            continue
        srv = suite.servers[key]
        tool = next((t for t in srv.tools if t.name == c.tool_name), None)
        args = _args(c)
        rule = tool.answer(args)[1] if tool else None
        calls.append(
            ScoredCall(
                server=key,
                tool=c.tool_name,
                args=args,
                selected_by=c.selected_by,
                agent=c.agent,
                rule=rule,
                distractor=srv.role == "distractor",
                failed=c.failed,
            )
        )
        if not c.failed:
            outputs.append(_text(c.output))
    score.calls = calls
    score.eval_calls = len(calls)
    score.distractor_calls = sum(c.distractor for c in calls)
    score.failed_calls = sum(c.failed for c in calls)
    score.wasted_calls = sum(1 for c in calls if c.distractor or c.rule is None or c.failed)
    score.precision = _ratio(score.eval_calls - score.wasted_calls, score.eval_calls)

    # -- expected calls
    for exp in scenario.expected_calls:
        same = [c for c in calls if c.server == exp.server and c.tool == exp.tool and not c.failed]
        right = [c for c in same if not exp.args or text_matches(exp.args, json.dumps(c.args, default=str))]
        pick = (right or same or [None])[0]
        score.expected.append(
            ExpectedCallResult(
                server=exp.server,
                tool=exp.tool,
                args=exp.args,
                chain=exp.chain,
                required=exp.required,
                called=bool(same),
                right_query=bool(right),
                selected_by=pick.selected_by if pick else None,
            )
        )
    req = [e for e in score.expected if e.required]
    chained = [e for e in score.expected if e.chain]
    score.tool_recall = _ratio(sum(e.called for e in req), len(req))
    score.query_accuracy = _ratio(sum(e.right_query for e in req), len(req))
    score.chain_recall = _ratio(sum(e.right_query for e in chained), len(chained))

    # -- facts: retrieved by a tool, then surfaced in answers / the summary
    answers = " ".join(_text(r.answer) + " " + _text(r.evidence) for r in inv.runs if r.question)
    summary_text = _text(summary)
    retrieved_text = " ".join(outputs)
    for fact in scenario.facts:
        score.facts.append(
            FactResult(
                id=fact.id,
                what=fact.what,
                chain=fact.chain,
                required=fact.required,
                retrieved=text_matches(fact.match, retrieved_text),
                in_answers=text_matches(fact.match, answers),
                in_summary=text_matches(fact.match, summary_text),
            )
        )
    req_facts = [f for f in score.facts if f.required]
    score.fact_retrieval = _ratio(sum(f.retrieved for f in req_facts), len(req_facts))
    score.fact_use = _ratio(sum(f.in_answers or f.in_summary for f in req_facts), len(req_facts))
    score.fact_in_summary = _ratio(sum(f.in_summary for f in req_facts), len(req_facts))
    score.lost_facts = [f.id for f in score.facts if f.retrieved and not (f.in_answers or f.in_summary)]

    score.composite = composite(score)
    return score


def composite(score: RunScore) -> float:
    """Weighted mean of the available component scores (missing ones are skipped)."""
    parts = {
        "verdict": score.verdict,
        "fact_use": score.fact_use,
        "tool_recall": score.tool_recall,
        "query_accuracy": score.query_accuracy,
        "chain_recall": score.chain_recall,
        "precision": score.precision if score.eval_calls else 0.0,
    }
    have = {k: v for k, v in parts.items() if v is not None}
    total = sum(WEIGHTS[k] for k in have)
    return round(sum(WEIGHTS[k] * v for k, v in have.items()) / total, 3) if total else 0.0


# ------------------------------------------------------------- aggregation
_NUMERIC = (
    "composite",
    "verdict",
    "tool_recall",
    "query_accuracy",
    "chain_recall",
    "precision",
    "fact_retrieval",
    "fact_use",
    "fact_in_summary",
    "eval_calls",
    "wasted_calls",
    "distractor_calls",
    "duration_s",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "llm_calls",
    "metered_tokens",
    "cost_usd",
)
_TRACE_NUMERIC = (
    "questions",
    "questions_chained",
    "total_tool_calls",
    "no_information_answers",
    "max_tools_per_question",
)


def _stats(values: list[float]) -> dict[str, float] | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {
        "mean": round(statistics.fmean(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "stdev": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
    }


def _billed(score: RunScore) -> int | None:
    return score.metered_tokens if score.metered_tokens is not None else score.total_tokens


def _budget(scores: list[RunScore], pricing: TokenPricing) -> dict[str, Any] | None:
    """Mean tokens/cost per investigation and how many fit in the free monthly allowance."""
    billed = [b for b in (_billed(s) for s in scores) if b]
    if not billed:
        return None
    mean = statistics.fmean(billed)
    return {
        "tokens_per_investigation": round(mean),
        "usd_per_investigation": round(mean * pricing.usd_per_million / 1e6, 2),
        "investigations_per_free_month": int(pricing.free_tokens_per_month // mean),
        "source": "metered" if all(s.metered_tokens is not None for s in scores) else "traces",
    }


def aggregate(scores: list[RunScore], pricing: TokenPricing = DEFAULT_PRICING) -> dict[str, Any]:
    """Per-scenario summary over repeated runs: score spread, verdicts, and how
    often each expected call / fact was hit -- the "what should have been used"
    view -- plus what was actually used instead, and what it cost."""
    out: dict[str, Any] = {}
    for scenario in dict.fromkeys(s.scenario for s in scores):
        runs = [s for s in scores if s.scenario == scenario]
        n = len(runs)
        row: dict[str, Any] = {"runs": n}
        for field in _NUMERIC:
            row[field] = _stats([getattr(r, field) for r in runs])
        for field in _TRACE_NUMERIC:
            row[field] = _stats([r.trace_metrics.get(field) for r in runs if r.trace_metrics])
        row["verdicts"] = dict(Counter(r.classification or "(none)" for r in runs))
        exp_keys = [(e.server, e.tool, e.args) for e in runs[0].expected] if runs else []
        row["expected_calls"] = [
            {
                "server": srv,
                "tool": tool,
                "args": args,
                "called": sum(
                    any(e.called for e in r.expected if (e.server, e.tool, e.args) == (srv, tool, args)) for r in runs
                )
                / n,
                "right_query": sum(
                    any(e.right_query for e in r.expected if (e.server, e.tool, e.args) == (srv, tool, args))
                    for r in runs
                )
                / n,
            }
            for srv, tool, args in exp_keys
        ]
        fact_ids = [f.id for f in runs[0].facts] if runs else []
        row["facts"] = [
            {
                "id": fid,
                "retrieved": sum(f.retrieved for r in runs for f in r.facts if f.id == fid) / n,
                "used": sum(f.in_answers or f.in_summary for r in runs for f in r.facts if f.id == fid) / n,
                "in_summary": sum(f.in_summary for r in runs for f in r.facts if f.id == fid) / n,
            }
            for fid in fact_ids
        ]
        row["tools_used"] = dict(Counter(f"{c.server}.{c.tool}" for r in runs for c in r.calls).most_common())
        row["selected_by"] = dict(Counter(c.selected_by for r in runs for c in r.calls))
        row["errors"] = [r.error for r in runs if r.error]
        row["budget"] = _budget(runs, pricing)
        out[scenario] = row
    overall = [s.composite for s in scores]
    return {
        "scenarios": out,
        "overall": _stats(overall),
        "budget": _budget(scores, pricing),
        "pricing": pricing.model_dump(),
        "runs": len(scores),
    }
