"""Typed models for dedicated-tenant multitenancy (``/api/3/tenants`` and status reads)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .base import BaseRecord


class Tenant(BaseRecord):
    """A tenant record from ``GET /api/3/tenants`` or ``POST /api/3/tenants``.

    ``isDedicated`` marks a tenant that runs on a remote FortiSOAR node; ``tenantId`` is the
    32-hex identity the remote node's agent record is keyed by. ``role`` is ``"self"`` for the
    master's own tenant.
    """

    name: str | None = None
    tenantId: str | None = None
    isDedicated: bool | None = None
    role: str | None = None
    description: str | None = None


class TenantStatus(BaseModel):
    """Health snapshot of one agent record, as :meth:`~pyfsr.api.multitenancy.MultitenancyAPI.status` reports it.

    Carries no secrets. ``configurationHealth`` and ``configstatus`` are the picklist
    ``itemValue`` strings (e.g. ``"Remote Node Connected"``, ``"Connection Verified"``).
    """

    model_config = ConfigDict(frozen=True)

    name: str | None = None
    uuid: str | None = None
    agentId: str | None = None
    active: bool | None = None
    configurationHealth: str | None = None
    configstatus: str | None = None
