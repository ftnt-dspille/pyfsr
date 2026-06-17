"""Execution-agent lifecycle + installer (``/api/3/agents`` and ``/api/integration``).

FortiSOAR *agents* are the remote/tenant execution nodes that proxy connector actions. This
wraps the agent **lifecycle** (list/get/create/delete), the **agent-installer** bundle
download, and **pushing a connector onto a specific agent**. The ``agentId`` of an active
agent is what feeds agent-scoped connector lookups
(``/api/integration/connector_details/?agent=<id>&active=true``). Accessed as
``client.agents``; the sibling **router** records an agent needs at create time are
``client.routers`` (see :class:`~pyfsr.api.routers.RoutersAPI`).

Creating a working agent is a two-step dance, mirroring the product:

1. ``create()`` registers the agent record (returns its ``uuid``/``agentId``).
2. ``installer()`` downloads the per-agent install bundle (a binary ``.bin``) that you then
   run on the agent host. Connectors can be baked into that bundle, or pushed later with
   ``install_connector()``.

Example::

    router = client.routers.first()                      # the configured secure-message router
    agent = client.agents.create("edge-1", router=router, installer_type="docker")
    blob = client.agents.installer(agent["agentId"])     # bytes — write to a .bin and run on host
    client.agents.install_connector(agent["agentId"], name="cyops_utilities", version="3.7.1")
    client.agents.delete(agent["uuid"])
"""

from __future__ import annotations

from typing import Any

from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/agents"
_AGENT_INSTALLER = "/api/integration/agent-installer/?format=json"
_INSTALL_CONNECTOR = "/api/integration/install-connector/?format=json"

# ``installerType`` is a system picklist; these are the stable fixture IRIs the in-product
# editor and the lab provisioner both rely on. Pass one of these keys ("bash"/"docker") to
# :meth:`AgentsAPI.create`, or a full ``/api/3/picklists/<uuid>`` IRI to override.
_INSTALLER_PICKLISTS = {
    "bash": "/api/3/picklists/a8181039-30a0-4807-b470-50de69d37561",
    "docker": "/api/3/picklists/d9f874be-3068-4282-9aed-100eba51e61b",
}

_ALL_LIMIT = 2147483647


class AgentsAPI(BaseAPI):
    """List, create, delete execution agents; download installers; push connectors."""

    def list(self, *, active_only: bool = False, limit: int = _ALL_LIMIT) -> list[dict[str, Any]]:
        """Return agent records (the ``hydra:member`` array).

        ``active_only=True`` filters to agents whose ``active`` flag is truthy. Each record
        carries at least ``uuid``, ``agentId``, and ``active``.
        """
        members = extract_members(self.client.get(_BASE, params={"$limit": limit}))
        if active_only:
            members = [a for a in members if isinstance(a, dict) and a.get("active")]
        return members

    def get(self, uuid: str) -> dict[str, Any]:
        """Fetch a single agent record by ``uuid`` (``GET /api/3/agents/{uuid}``)."""
        uuid = _require_uuid(uuid, "get")
        return self.client.get(f"{_BASE}/{uuid}")

    def create(
        self,
        name: str,
        *,
        router: str | dict[str, Any],
        installer_type: str = "docker",
        description: str = "",
    ) -> dict[str, Any]:
        """Register a new agent (``POST /api/3/agents``); returns the created record.

        ``router`` is the secure-message-exchange router the agent connects through — pass a
        router record (from :meth:`RoutersAPI.first`/``list``), its ``@id`` IRI, or its bare
        uuid. ``installer_type`` is ``"docker"`` (default) or ``"bash"``, or a full picklist
        IRI. Creating the record does **not** install anything; follow with :meth:`installer`.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("create() requires a non-empty agent name")
        body = {
            "name": name,
            "router": _as_router_iri(router),
            "installerType": _INSTALLER_PICKLISTS.get(installer_type, installer_type),
            "description": description,
        }
        return self.client.post(_BASE, data=body)

    def delete(self, uuid: str) -> None:
        """Delete an agent record (``DELETE /api/3/agents/{uuid}``). Sends no body."""
        uuid = _require_uuid(uuid, "delete")
        self.client.delete(f"{_BASE}/{uuid}")

    def installer(
        self,
        agent_id: str,
        *,
        connectors: list[Any] | None = None,
        include_last_known_configurations: bool = False,
    ) -> bytes:
        """Download the per-agent install bundle (a binary ``.bin``) as ``bytes``.

        ``POST /api/integration/agent-installer/?format=json``. ``agent_id`` is the agent's
        ``agentId`` (not its record uuid). ``connectors`` is an optional list of connectors to
        bake into the bundle; ``include_last_known_configurations`` ships the agent's last
        known connector configs. Returns the raw bytes — write them to a ``.bin`` and run it
        on the agent host.
        """
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("installer() requires a non-empty agent_id")
        body = {
            "agent": agent_id,
            "connectors": list(connectors or []),
            "include_last_known_configurations": bool(include_last_known_configurations),
        }
        resp = self.client.request("POST", _AGENT_INSTALLER, data=body)
        return resp.content

    def install_connector(
        self,
        agent_id: str,
        *,
        name: str,
        version: str,
        label: str | None = None,
        category: list[str] | None = None,
        description: str = "",
        publisher: str = "Fortinet",
        rpm_full_name: str = "",
    ) -> dict[str, Any]:
        """Register/activate a connector on a specific agent.

        ``POST /api/integration/install-connector/?format=json`` — the call the FSoC *Agents →
        Connectors* view makes so the connector shows as installed on ``agent_id``. ``name``
        and ``version`` **must match the appliance's connector catalog** (look it up via
        ``GET /api/integration/connectors/?name=<name>&format=json``); a version the catalog
        doesn't know returns a bare 500. Poll :meth:`connector_install_status` for progress.
        """
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("install_connector() requires a non-empty agent_id")
        body = {
            "name": name,
            "label": label or name,
            "version": version,
            "rpm_full_name": rpm_full_name,
            "category": list(category or []),
            "description": description,
            "publisher": publisher,
            "agent": [agent_id],
        }
        return self.client.post(_INSTALL_CONNECTOR, data=body)

    def connector_install_status(
        self,
        connector: str,
        version: str,
        *,
        agent_id: str | None = None,
        active: bool = True,
    ) -> list[dict[str, Any]]:
        """Per-agent connector install status rows (awaiting → in-progress → Completed).

        ``POST /api/integration/connectors/agents/<connector>/<version>/?format=json`` — this
        endpoint is **POST-only** (a GET is forbidden) and an empty body is enough. Returns
        the list of agent×version rows; pass ``agent_id`` to keep only that agent's row.
        """
        path = f"/api/integration/connectors/agents/{connector}/{version}/?format=json"
        if active:
            path += "&active=true"
        rows = self.client.post(path, data={})
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("hydra:member") or []
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        if agent_id is not None:
            rows = [r for r in rows if r.get("agent") == agent_id]
        return rows


def _require_uuid(uuid: str, op: str) -> str:
    if not isinstance(uuid, str) or not uuid.strip():
        raise ValueError(f"{op}() requires a non-empty agent uuid")
    return uuid.strip()


def _as_router_iri(router: str | dict[str, Any]) -> str:
    """Normalize a router (record dict, ``@id`` IRI, or bare uuid) to its ``/api/3/routers``
    IRI for the agent create payload."""
    if isinstance(router, dict):
        iri = router.get("@id") or router.get("uuid")
        if not iri:
            raise ValueError("router dict has neither '@id' nor 'uuid'")
        router = iri
    if not isinstance(router, str) or not router.strip():
        raise ValueError("create() requires a router (record, @id IRI, or uuid)")
    router = router.strip()
    return router if router.startswith("/api/") else f"/api/3/routers/{router}"
