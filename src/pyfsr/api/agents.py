"""Agent listing (``/api/3/agents``).

FortiSOAR *agents* are the remote/tenant execution nodes that proxy connector actions. This
thin wrapper lists them; the ``agentId`` of an active agent is what feeds agent-scoped
connector lookups (``/api/integration/connector_details/?agent=<id>&active=true``). Accessed
as ``client.agents``.

Example::

    client.agents.list()                  # all agents (raw records)
    client.agents.list(active_only=True)   # only currently-active agents
"""

from __future__ import annotations

from typing import Any

from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/agents"


class AgentsAPI(BaseAPI):
    """List execution agents."""

    def list(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Return agent records (the ``hydra:member`` array).

        ``active_only=True`` filters to agents whose ``active`` flag is truthy. Each record
        carries at least ``agentId`` and ``active``.
        """
        members = extract_members(self.client.get(_BASE))
        if active_only:
            members = [a for a in members if isinstance(a, dict) and a.get("active")]
        return members
