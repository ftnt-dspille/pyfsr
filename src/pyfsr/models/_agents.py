"""Typed models for the FortiSOAR agent API (``/api/3/agents``)."""

from __future__ import annotations

from typing import Any

from .base import BaseRecord


class Agent(BaseRecord):
    """An agent record from ``GET /api/3/agents`` or ``POST /api/3/agents``.

    Core fields typed; operational metadata (installer bytes, SME config, etc.)
    preserved in ``extra``.
    """

    uuid: str | None = None
    agentId: str | None = None
    name: str | None = None
    active: bool | None = None
    description: str | None = None
    created: str | None = None
    modified: str | None = None
    router: Any = None  # Router entity relationship — IRI or expanded dict
    installerType: str | None = None  # picklist IRI /api/3/picklists/...
    configurationHealth: str | None = None  # picklist IRI /api/3/picklists/...


class AgentConnectorStatus(BaseRecord):
    """A single row from ``connector_install_status()``.

    Returned by ``POST /api/integration/connectors/agents/<name>/<version>/``.
    ``status`` progresses through ``"awaiting"`` → ``"in-progress"`` → ``"Completed"``.
    """

    agent: str | None = None
    agentId: str | None = None
    name: str | None = None
    version: str | None = None
    status: str | None = None
    label: str | None = None
    errorMessage: str | None = None
    progressPercent: int | None = None
