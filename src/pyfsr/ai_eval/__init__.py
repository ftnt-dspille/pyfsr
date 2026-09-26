"""Repeatable, scored evaluation of FortiAI alert investigations (8.0.1+).

A *suite* defines stub MCP servers with planted ground truth (a SIEM, an EDR,
identity, CMDB, threat intel, plus distractors) and *scenarios* -- an alert, the
tool calls a good investigation makes, the facts it should surface and the
right verdict. :func:`~pyfsr.ai_eval.runner.deploy` hosts the stubs on the appliance through the
bundled ``fsr-ai-eval-stub`` connector (a local listener, like the Teams
connector's bot listener) and registers each as an MCP server;
:func:`~pyfsr.ai_eval.runner.run_suite` runs the investigations and scores each one from its trace.

CLI: ``pyfsr ai-eval deploy|run|log|teardown``.
"""

from .runner import CONNECTOR, Deployment, call_log, create_alert, deploy, run_scenario, run_suite, teardown
from .scenario import DEFAULT_SUITE, Scenario, StubServer, StubTool, Suite, load_suite
from .score import DEFAULT_PRICING, WEIGHTS, RunScore, TokenPricing, aggregate, score_run

__all__ = [
    "CONNECTOR",
    "DEFAULT_PRICING",
    "DEFAULT_SUITE",
    "WEIGHTS",
    "Deployment",
    "RunScore",
    "Scenario",
    "StubServer",
    "StubTool",
    "Suite",
    "TokenPricing",
    "aggregate",
    "call_log",
    "create_alert",
    "deploy",
    "load_suite",
    "run_scenario",
    "run_suite",
    "score_run",
    "teardown",
]
