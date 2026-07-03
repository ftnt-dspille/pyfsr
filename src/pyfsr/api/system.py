"""Appliance introspection + licensing (version, permissions, features, license, cluster).

Read-only probes the UI uses for gating, plus license deploy/inspect. Accessed
as ``client.system``.

Example:
    >>> client.system.version()                 # build version (public)
    >>> client.system.permissions()             # caller's effective permissions
    >>> client.system.feature_access()          # license-tier feature flags
    >>> client.system.cluster_health()          # per-node HA health
    >>> client.system.license()                 # deployed license state
"""

from __future__ import annotations

from typing import Any

from ..models._system import DailyActionCount
from .base import BaseAPI


class SystemAPI(BaseAPI):
    """Version, permissions, feature flags, cluster health, and licensing."""

    def version(self) -> dict[str, Any]:
        """Build version (``GET /api/version``). Public — no auth required."""
        return self.client.get("/api/version")

    def permissions(self) -> dict[str, Any]:
        """The caller's effective permissions (``GET /api/permissions/current``).

        A module -> ``{create, read, update, delete, execute}`` boolean map —
        authoritative for UI/automation gating.
        """
        return self.client.get("/api/permissions/current")

    def feature_access(self) -> dict[str, Any]:
        """License-tier feature-flag map (``GET /api/product/feature-access``).

        Each key is a product feature; the boolean says whether the current
        license unlocks it. Gate paths off this instead of hard-coding tiers.
        """
        return self.client.get("/api/product/feature-access")

    def cluster_health(self) -> Any:
        """Per-node HA cluster health (``GET /api/auth/cluster/health``).

        One object per node (status, services, connectivity, cpu/memory/disk,
        replication, …). JWT auth only on tested appliances.
        """
        return self.client.get("/api/auth/cluster/health")

    # ----------------------------------------------------------------- license
    def license(self, *, node_id: str | None = None, param: str | None = None) -> dict[str, Any]:
        """Current license state (``GET /api/auth/license``).

        Reports the deployed license for the cluster, or one node with
        ``node_id``. Authenticated equivalent of the public ``get_info`` flow.
        """
        params: dict[str, Any] = {}
        if node_id is not None:
            params["node_id"] = node_id
        if param is not None:
            params["param"] = param
        return self.client.get("/api/auth/license", params=params or None)

    def daily_action_count(self) -> DailyActionCount:
        """Daily action-count license usage (``GET /api/wf/workflow/config/?section=license``).

        Returns a typed :class:`~pyfsr.models.DailyActionCount` (dict-compatible)
        with the workflow engine's decrypted license counters:
        ``daily_action_limit``, ``remaining_actions``, ``reset_time``,
        ``last_update_time``, plus ``.enforced`` and ``.used_today`` helpers.

        ``daily_action_limit`` is the per-day cap (e.g. 10000 on FortiFlex Starter;
        ``-1`` means unlimited/unenforced). Counted steps are Create/Update Record,
        Connector Action, Set Variable, etc.; Wait/Approval/Loops/Reference-a-
        Playbook are not counted. This is the endpoint the UI's
        ``getDailyActionCount`` calls.
        """
        resp = self.client.get("/api/wf/workflow/config/", params={"section": "license"})
        return DailyActionCount.model_validate(resp if isinstance(resp, dict) else {})

    def deploy_license(self, license_key: str) -> dict[str, Any]:
        """Deploy a license over an already-active one (``POST /api/auth/license``).

        Authenticated renewal/replacement — requires a previously valid license
        to be active. For first-time activation on a fresh appliance use
        :meth:`deploy_license_public`.
        """
        return self.client.post("/api/auth/license", data={"license_key": license_key})

    def deploy_license_public(self, license_key: str, *, node_id: str | None = None) -> dict[str, Any]:
        """First-time license activation (``POST /api/public/license``, no auth).

        Installs a license on a fresh/unlicensed appliance (``action:
        deploy_license``). Public since 7.0.0.
        """
        body: dict[str, Any] = {"action": "deploy_license", "license_key": license_key}
        if node_id is not None:
            body["nodeId"] = node_id
        return self.client.post("/api/public/license", data=body)

    def license_info_public(self, *, node_id: str | None = None) -> dict[str, Any]:
        """Public license status/info (``POST /api/public/license`` ``get_info``)."""
        body: dict[str, Any] = {"action": "get_info"}
        if node_id is not None:
            body["nodeId"] = node_id
        return self.client.post("/api/public/license", data=body)
