"""Appliance introspection + licensing (version, permissions, features, license, cluster).

Read-only probes the UI uses for gating, plus license deploy/inspect. Accessed
as ``client.system``.

Example:
    >>> client = demo_client()
    >>> client.system.version()["version"]
    '8.0.0-6034'
    >>> client.system.feature_access()["automation"]
    True
"""

from __future__ import annotations

from typing import Any

from ..models._system import DailyActionCount
from .base import BaseAPI


class SystemAPI(BaseAPI):
    """Version, permissions, feature flags, cluster health, and licensing."""

    def version(self) -> dict[str, Any]:
        """Build version (``GET /api/version``). Public — no auth required.

        Example:
            >>> client = demo_client()
            >>> client.system.version()["version"]
            '8.0.0-6034'
        """
        return self.client.get("/api/version")

    def permissions(self) -> dict[str, Any]:
        """The caller's effective permissions (``GET /api/permissions/current``).

        A module -> ``{create, read, update, delete, execute}`` boolean map —
        authoritative for UI/automation gating.

        Example:
            >>> client = demo_client()
            >>> perms = client.system.permissions()
            >>> perms["alerts"]["read"]
            True
            >>> perms["people"]["create"]
            False
        """
        return self.client.get("/api/permissions/current")

    def feature_access(self) -> dict[str, Any]:
        """License-tier feature-flag map (``GET /api/product/feature-access``).

        Each key is a product feature; the boolean says whether the current
        license unlocks it. Gate paths off this instead of hard-coding tiers.

        Example:
            >>> client = demo_client()
            >>> features = client.system.feature_access()
            >>> features["automation"]
            True
            >>> features["endpoint_management"]
            False
        """
        return self.client.get("/api/product/feature-access")

    def cluster_health(self) -> Any:
        """Per-node HA cluster health (``GET /api/auth/cluster/health``).

        One object per node (status, services, connectivity, cpu/memory/disk,
        replication, …). JWT auth only on tested appliances.

        Example:
            >>> client = demo_client_jwt()
            >>> health = client.system.cluster_health()
            >>> health[0]["status"]
            'Active'

        .. note::
            Requires JWT auth — raises ``UnsupportedAuthOperationError`` under
            ``demo_client()``'s ``APIKeyAuth`` (hence ``demo_client_jwt()`` here).
        """
        return self.client.get("/api/auth/cluster/health")

    # ----------------------------------------------------------------- license
    def license(self, *, node_id: str | None = None, param: str | None = None) -> dict[str, Any]:
        """Current license state (``GET /api/auth/license``).

        Reports the deployed license for the cluster, or one node with
        ``node_id``. Authenticated equivalent of the public ``get_info`` flow.

        Example:
            >>> client = demo_client_jwt()
            >>> lic = client.system.license()
            >>> lic["license_type"]
            'FortiFlex'

        .. note::
            Requires JWT auth — raises ``UnsupportedAuthOperationError`` under
            ``demo_client()``'s ``APIKeyAuth`` (hence ``demo_client_jwt()`` here).
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

        Example:
            >>> client = demo_client()
            >>> dac = client.system.daily_action_count()
            >>> dac.daily_action_limit
            10000
            >>> dac.enforced
            True
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

    # -- FortiFlex ---------------------------------------------------------

    def install_flex_license(self, license_token: str, *, node_id: str | None = None) -> dict[str, Any]:
        """Submit a FortiFlex token for redemption (``POST /api/public/license``
        ``install_flex_license``). Unauthenticated — works under the
        ``FSR-Auth-018`` duplicate-license lockout.

        Unlike :meth:`deploy_license` / :meth:`deploy_license_public`, which take
        a *signed license JWT*, this takes the short FortiFlex entitlement token
        and has the appliance redeem it against the licensing server (token →
        license, server-side). Redemption is asynchronous — poll
        :meth:`flex_license_status`, or use :meth:`deploy_flex_license` which
        does both. **FortiFlex tokens are single-use**: a failed redemption
        spends the token; mint a fresh one (from an ACTIVE entitlement) to retry.
        """
        body: dict[str, Any] = {"action": "install_flex_license", "data": {"license_token": license_token}}
        if node_id is not None:
            body["nodeId"] = node_id
        return self.client.post("/api/public/license", data=body)

    def flex_license_status(self, *, node_id: str | None = None) -> dict[str, Any]:
        """Current FortiFlex deployment status (``POST /api/public/license``
        ``get_status``). Carries ``depl_status`` (``in_progress`` / ``finished``
        / ``failed``) and ``depl_message``."""
        body: dict[str, Any] = {"action": "get_status"}
        if node_id is not None:
            body["nodeId"] = node_id
        return self.client.post("/api/public/license", data=body)

    def deploy_flex_license(
        self, license_token: str, *, node_id: str | None = None, timeout: float = 600, poll_interval: float = 5
    ) -> dict[str, Any]:
        """Redeem a FortiFlex token and wait for the deployment to resolve.

        Submits the token (:meth:`install_flex_license`) then polls
        :meth:`flex_license_status` until ``depl_status`` is ``finished`` or
        ``failed`` (or ``timeout`` seconds elapse). Returns
        ``{"ok", "depl_status", "depl_message", "install", "polls"}`` — ``ok`` is
        True only on a ``finished`` deployment. Never raises on a ``failed``
        redemption; the failure is reported in the returned dict (the token is
        spent regardless — mint a fresh one to retry).

        On a license-locked appliance no credential authenticates, so build the
        client with ``public=True`` (a no-auth client for the unauthenticated
        ``/api/public/license`` endpoint):

        Example:
            >>> client = FortiSOAR("https://soar.example.com", public=True, verify_ssl=False)  # doctest: +SKIP
            >>> res = client.system.deploy_flex_license("<token>")  # doctest: +SKIP
            >>> res["ok"], res["depl_status"]  # doctest: +SKIP
            (True, 'finished')
        """
        import time

        install = self.install_flex_license(license_token, node_id=node_id)
        deadline = time.monotonic() + timeout
        polls = 0
        depl_status = None
        depl_message = None
        while time.monotonic() < deadline:
            polls += 1
            status = self.flex_license_status(node_id=node_id)
            depl_status = (status or {}).get("depl_status")
            depl_message = (status or {}).get("depl_message")
            if depl_status in ("finished", "failed"):
                break
            time.sleep(poll_interval)
        return {
            "ok": depl_status == "finished",
            "depl_status": depl_status,
            "depl_message": depl_message,
            "install": install,
            "polls": polls,
        }
