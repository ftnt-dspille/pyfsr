"""pyfsr's AI/agent surface — grouped, not flattened into the top-level package.

Three cohesive pieces for driving a live FortiSOAR from an LLM agent:

* :mod:`pyfsr.agent.tools` — a framework-agnostic registry of FortiSOAR
  operations as JSON-Schema tool definitions, plus :func:`pyfsr.agent.tools.dispatch` to execute a
  tool call against a live :class:`~pyfsr.FortiSOAR` client and adapters for the
  Anthropic / OpenAI tool formats.
* :mod:`pyfsr.agent.mcp` — a generic Model Context Protocol server over that
  registry (``python -m pyfsr.agent.mcp``).
* :mod:`pyfsr.agent.archetypes` — use-case → FortiSOAR-artifact archetypes
  (module schema + connector manifest + playbook skeletons) and the router that
  classifies a free-text use case, exposed to agents via the ``map_use_case`` tool.

Import the registry from here (``from pyfsr.agent import dispatch, tool_schemas``)
rather than the old top-level ``pyfsr.tools``.
"""

from __future__ import annotations

from .tools import (
    REGISTRY,
    ToolSpec,
    dispatch,
    get_tool,
    list_tools,
    to_anthropic_tools,
    to_openai_tools,
    tool_schemas,
)

__all__ = [
    "REGISTRY",
    "ToolSpec",
    "dispatch",
    "get_tool",
    "list_tools",
    "to_anthropic_tools",
    "to_openai_tools",
    "tool_schemas",
]
