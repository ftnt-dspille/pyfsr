"""``pyfsr ai-eval`` -- scored, repeatable FortiAI investigation runs (8.0.1+).

Subcommands:

- ``deploy`` -- install the stub connector, load the suite's fixtures, register
  its MCP servers and allow them for the suite's agents (idempotent).
- ``run [--scenario ID ...] [--runs N] [--out FILE]`` -- investigate each
  scenario's alert N times and print the scorecard.
- ``log [--clear]`` -- the stub servers' own call log.
- ``teardown [--uninstall]`` -- unregister the suite's MCP servers.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from .playbook import _make_client, add_connection_args

if TYPE_CHECKING:
    from ..ai_eval import RunScore, Suite, TokenPricing
    from ..client import FortiSOAR


def _client(args: argparse.Namespace) -> FortiSOAR:
    if getattr(args, "instance", None):
        from ..instances import InstanceRegistry

        client = InstanceRegistry.load().client(args.instance)
    else:
        client = _make_client(args)
    print(f"target: {client.base_url}", file=sys.stderr)
    return client


def _suite(args: argparse.Namespace) -> Suite:
    from ..ai_eval import load_suite

    return load_suite(args.suite)


def _pct(value: Any) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _mean(stat: dict[str, float] | None, pct: bool = True) -> str:
    if not stat:
        return "-"
    if pct:
        spread = f" ±{stat['stdev'] * 100:.0f}" if stat.get("stdev") else ""
        return f"{stat['mean'] * 100:.0f}%{spread}"
    return f"{stat['mean']:g}"


def _print_run(score: RunScore) -> None:
    if score.error:
        print(f"  [{score.scenario}] ERROR {score.error}", flush=True)
        return
    print(
        f"  [{score.scenario}] task={str(score.task_id)[:8]} verdict={score.classification!r} "
        f"score={score.composite:.2f} tools={_pct(score.tool_recall)} query={_pct(score.query_accuracy)} "
        f"pivot={_pct(score.chain_recall)} facts {_pct(score.fact_retrieval)} got/{_pct(score.fact_use)} used "
        f"calls={score.eval_calls} wasted={score.wasted_calls} ({score.duration_s}s) "
        f"tokens={_tokens(score)} ${score.cost_usd if score.cost_usd is not None else '-'}",
        flush=True,
    )


def _tokens(score: RunScore) -> str:
    traced = f"{score.total_tokens:,}" if score.total_tokens is not None else "-"
    return traced if score.metered_tokens is None else f"{traced} (metered {score.metered_tokens:,})"


def _pricing(args: argparse.Namespace) -> TokenPricing:
    from ..ai_eval import TokenPricing

    return TokenPricing(usd_per_million=args.usd_per_million, free_tokens_per_month=args.free_tokens)


def _print_budget(budget: dict[str, Any] | None, indent: str = "  ") -> None:
    if budget:
        print(
            f"{indent}cost: {budget['tokens_per_investigation']:,} tokens/investigation ({budget['source']}) "
            f"= ${budget['usd_per_investigation']}  -> {budget['investigations_per_free_month']} "
            "investigations fit the free monthly allowance"
        )


def print_report(summary: dict[str, Any]) -> None:
    for scenario, row in summary["scenarios"].items():
        print(f"\n=== {scenario}  ({row['runs']} run(s))")
        print(
            f"score {_mean(row['composite'])}  verdict {_mean(row['verdict'])} {row['verdicts']}  "
            f"tool recall {_mean(row['tool_recall'])}  right query {_mean(row['query_accuracy'])}  "
            f"pivot {_mean(row['chain_recall'])}  precision {_mean(row['precision'])}"
        )
        print(
            f"facts retrieved {_mean(row['fact_retrieval'])}  used {_mean(row['fact_use'])}  "
            f"in summary {_mean(row['fact_in_summary'])}  | questions {_mean(row['questions'], False)}  "
            f"chained q {_mean(row['questions_chained'], False)}  no-info {_mean(row['no_information_answers'], False)}"
        )
        print("  expected call                                    called  right-query")
        for e in row["expected_calls"]:
            label = f"{e['server']}.{e['tool']}({e['args']})"
            print(f"  {label[:48]:48} {e['called'] * 100:5.0f}%  {e['right_query'] * 100:5.0f}%")
        print("  fact           retrieved  used  in-summary")
        for f in row["facts"]:
            print(
                f"  {f['id']:14} {f['retrieved'] * 100:8.0f}%  {f['used'] * 100:4.0f}%  {f['in_summary'] * 100:6.0f}%"
            )
        print(f"  tools used: {row['tools_used']}")
        print(f"  chosen by: {row['selected_by']}")
        _print_budget(row.get("budget"))
    if summary.get("overall"):
        print(f"\nOVERALL composite: {_mean(summary['overall'])} over {summary['runs']} run(s)")
        _print_budget(summary.get("budget"), indent="")


def cmd_deploy(args: argparse.Namespace) -> int:
    from ..ai_eval import deploy

    dep = deploy(_client(args), _suite(args), port=args.listener_port, install=not args.no_install)
    print(json.dumps(dep.model_dump(), indent=2, default=str))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from ..ai_eval import run_suite

    client, suite = _client(args), _suite(args)
    report = run_suite(
        client,
        suite,
        runs=args.runs,
        scenarios=args.scenario or None,
        timeout=args.timeout,
        on_run=_print_run,
        meter=not args.no_meter,
        pricing=_pricing(args),
    )
    print_report(report["summary"])
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nfull report: {args.out}")
    return 0


def cmd_cost(args: argparse.Namespace) -> int:
    """Token use and cost of finished investigations, per agent."""
    client, pricing = _client(args), _pricing(args)
    profiles = {c.uuid: c for c in client.ai.list_llm_configs()}
    default = next((c for c in profiles.values() if c.isdefault), None)
    if default:
        print(f"default LLM profile: {default.name} = {default.provider}/{default.modelname}")
    totals = []
    for task_id in args.task_id:
        inv = client.ai.traces.investigation(task_id)
        t = inv.tokens
        totals.append(t.get("total_tokens") or 0)
        cost = pricing.cost(t.get("input_tokens") or 0, t.get("output_tokens") or 0)
        print(
            f"\n{task_id}: {t.get('total_tokens', 0):,} tokens ({t.get('input_tokens', 0):,} in / "
            f"{t.get('output_tokens', 0):,} out, {t.get('llm_calls', 0)} LLM calls, {len(inv.runs)} runs) = ${cost}"
        )
        for agent, row in inv.tokens_by_agent().items():
            print(f"  {agent:28} {row['total_tokens']:>8,}  ({row['llm_calls']} LLM calls)")
    if totals:
        mean = sum(totals) / len(totals)
        print(
            f"\nmean {mean:,.0f} tokens = ${mean * pricing.usd_per_million / 1e6:.2f} per investigation; "
            f"{int(pricing.free_tokens_per_month // mean) if mean else '-'} fit in "
            f"{pricing.free_tokens_per_month:,} free tokens/month"
        )
    return 0


def cmd_balance(args: argparse.Namespace) -> int:
    """FortiAI token allowance and what is left."""
    bal = _client(args).ai.token_balance(args.llm_config)
    print(
        f"entitled {bal.entitled_tokens:,} (+{bal.entitled_topup_tokens:,} top-up)  remaining {bal.remaining:,}  "
        f"used {bal.used_tokens:,}  [{bal.type} {bal.sn}]"
    )
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    from ..ai_eval import call_log

    for row in call_log(_client(args), clear=args.clear):
        print(json.dumps(row, default=str))
    return 0


def cmd_teardown(args: argparse.Namespace) -> int:
    from ..ai_eval import teardown

    teardown(_client(args), _suite(args), uninstall=args.uninstall)
    print("removed")
    return 0


def _common(p: argparse.ArgumentParser) -> None:
    add_connection_args(p)
    p.add_argument("--instance", help="alias from ~/.pyfsr/instances.toml (overrides the connection flags)")
    p.add_argument("--suite", help="suite YAML (default: the bundled suite)")


def _pricing_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--usd-per-million",
        type=float,
        default=200.0,
        help="USD per 1M tokens (default 200: FortiAI top-ups are $100 per 500k)",
    )
    p.add_argument("--free-tokens", type=int, default=5_000_000, help="free tokens per month (default 5,000,000)")


def build_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("deploy", help="host the stub MCP servers on the appliance and allow them for agents")
    _common(p)
    p.add_argument("--listener-port", type=int, default=18900, help="127.0.0.1 port for the stub listener")
    p.add_argument("--no-install", action="store_true", help="skip re-installing the connector")
    p.set_defaults(func=cmd_deploy)

    p = sub.add_parser("run", help="run scored investigations")
    _common(p)
    p.add_argument("--scenario", action="append", help="scenario id (repeatable; default: all)")
    p.add_argument("--runs", type=int, default=1, help="investigations per scenario (default 1)")
    p.add_argument("--timeout", type=float, default=1200.0, help="seconds per investigation")
    p.add_argument("--out", help="write the full JSON report here")
    p.add_argument("--no-meter", action="store_true", help="don't read the FortiAI token balance before/after each run")
    _pricing_args(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("cost", help="token use and cost of finished investigations, per agent")
    _common(p)
    p.add_argument("task_id", nargs="+", help="investigation task id(s)")
    _pricing_args(p)
    p.set_defaults(func=cmd_cost)

    p = sub.add_parser("balance", help="FortiAI token allowance and what is left")
    _common(p)
    p.add_argument("--llm-config", help="LLM profile name or uuid (default: the default profile)")
    p.set_defaults(func=cmd_balance)

    p = sub.add_parser("log", help="print the stub servers' call log")
    _common(p)
    p.add_argument("--clear", action="store_true", help="truncate the log after reading")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("teardown", help="unregister the suite's MCP servers")
    _common(p)
    p.add_argument("--uninstall", action="store_true", help="also stop the listener and uninstall the connector")
    p.set_defaults(func=cmd_teardown)
