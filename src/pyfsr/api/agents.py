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

Example:
    >>> client = demo_client()
    >>> router = "/api/3/routers/3a2b1c0d-9e8f-4a7b-6c5d-4e3f2a1b0c9d"
    >>> agent = client.agents.create("edge-1", router=router, installer_type="docker")
    >>> agent.agentId
    'edge-1'
    >>> client.agents.install_connector(agent.agentId, name="cyops_utilities", version="3.7.1")
    {'result': 'Success'}
    >>> client.agents.delete(agent.uuid)
"""

from __future__ import annotations

from typing import Any

from ..models._agents import Agent, AgentConnectorStatus
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

    def list(self, *, active_only: bool = False, limit: int = _ALL_LIMIT) -> list[Agent]:
        """Return agent records (the ``hydra:member`` array).

        ``active_only=True`` filters to agents whose ``active`` flag is truthy. Each record
        carries at least ``uuid``, ``agentId``, and ``active``.

        Example:
            >>> client = demo_client()
            >>> agents = client.agents.list()
            >>> agents[0].agentId
            'edge-1'
        """
        members = extract_members(self.client.get(_BASE, params={"$limit": limit}))
        agents = [Agent.model_validate(a) for a in members if isinstance(a, dict)]
        if active_only:
            agents = [a for a in agents if a.active]
        return agents

    def get(self, uuid: str) -> Agent:
        """Fetch a single agent record by ``uuid`` (``GET /api/3/agents/{uuid}``).

        Example:
            >>> client = demo_client()
            >>> client.agents.get("6f5e4d3c-2b1a-4c9d-8e7f-1a2b3c4d5e6f").name
            'edge-1'
        """
        uuid = _require_uuid(uuid, "get")
        resp = self.client.get(f"{_BASE}/{uuid}")
        return Agent.model_validate(resp if isinstance(resp, dict) else {"uuid": uuid})

    def create(
        self,
        name: str,
        *,
        router: str | dict[str, Any],
        installer_type: str = "docker",
        description: str = "",
    ) -> Agent:
        """Register a new agent (``POST /api/3/agents``); returns the created record.

        ``router`` is the secure-message-exchange router the agent connects through — pass a
        router record (from :meth:`~pyfsr.api.routers.RoutersAPI.first`/``list``), its ``@id``
        IRI, or its bare uuid. ``installer_type`` is ``"docker"`` (default) or ``"bash"``, or a
        full picklist IRI. Creating the record does **not** install anything; follow with
        :meth:`installer`.

        Example:
            >>> client = demo_client()
            >>> router = "/api/3/routers/3a2b1c0d-9e8f-4a7b-6c5d-4e3f2a1b0c9d"
            >>> client.agents.create("edge-1", router=router).agentId
            'edge-1'
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("create() requires a non-empty agent name")
        body = {
            "name": name,
            "router": _as_router_iri(router),
            "installerType": _INSTALLER_PICKLISTS.get(installer_type, installer_type),
            "description": description,
        }
        resp = self.client.post(_BASE, data=body)
        return Agent.model_validate(resp if isinstance(resp, dict) else {})

    def delete(self, uuid: str) -> None:
        """Delete an agent record (``DELETE /api/3/agents/{uuid}``). Sends no body.

        Example:
            >>> client = demo_client()
            >>> client.agents.delete("6f5e4d3c-2b1a-4c9d-8e7f-1a2b3c4d5e6f")
        """
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

        Example:
            >>> client = demo_client()
            >>> blob = client.agents.installer("edge-1")
            >>> isinstance(blob, bytes)
            True
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

        Example:
            >>> client = demo_client()
            >>> client.agents.install_connector("edge-1", name="cyops_utilities", version="3.7.1")
            {'result': 'Success'}
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

    def upgrade_connector(self, agent_id: str, *, name: str, version: str) -> dict[str, Any]:
        """Upgrade an installed connector on a remote agent to ``version``.

        ``PUT /api/integration/install-connector/`` — same body shape the install
        proxy uses (``{name, version, agent_id}``). The appliance proxies the
        upgrade to ``agent_id`` over SME; poll :meth:`connector_install_status`
        for progress. To go the other way (downgrade/reinstall) pass the target
        ``version`` explicitly.

        Example:
            >>> client = demo_client()
            >>> client.agents.upgrade_connector("edge-1", name="cyops_utilities", version="3.8.0")
            {'result': 'Success'}
        """
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("upgrade_connector() requires a non-empty agent_id")
        body = {"name": name, "version": version, "agent_id": agent_id}
        return self.client.put(_INSTALL_CONNECTOR, data=body)

    def uninstall_connector(self, agent_id: str, *, name: str, version: str) -> dict[str, Any]:
        """Uninstall a connector from a remote agent.

        ``DELETE /api/integration/install-connector/`` with ``{name, version,
        agent_id}``. Distinct from the appliance-level
        :meth:`~pyfsr.api.connectors.ConnectorsAPI.uninstall`, which removes a
        connector from the appliance's self-agent by integer id.

        Example:
            >>> client = demo_client()
            >>> client.agents.uninstall_connector("edge-1", name="cyops_utilities", version="3.7.1")
            {'result': 'Success'}
        """
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("uninstall_connector() requires a non-empty agent_id")
        body = {"name": name, "version": version, "agent_id": agent_id}
        resp = self.client.request("DELETE", _INSTALL_CONNECTOR, data=body)
        try:
            return resp.json()
        except ValueError:
            return {}

    def heartbeat(self, agent_id: str) -> dict[str, Any]:
        """Probe a remote agent's liveness over the secure-message bus.

        ``GET /api/integration/agent-heartbeat/{agent}/`` — round-trips a
        heartbeat to the named agent and returns its response. This reflects the
        *current* SME-bus state, independent of the agent record's asynchronously
        updated ``configurationHealth.itemValue`` field.

        Example:
            >>> client = demo_client()
            >>> client.agents.heartbeat("edge-1")["status"]
            'alive'
        """
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("heartbeat() requires a non-empty agent_id")
        resp = self.client.get(f"/api/integration/agent-heartbeat/{agent_id}/")
        return resp if isinstance(resp, dict) else {"result": resp}

    def connector_install_status(
        self,
        connector: str,
        version: str,
        *,
        agent_id: str | None = None,
        active: bool = True,
    ) -> list[AgentConnectorStatus]:
        """Per-agent connector install status rows (awaiting → in-progress → Completed).

        ``POST /api/integration/connectors/agents/<connector>/<version>/?format=json`` — this
        endpoint is **POST-only** (a GET is forbidden) and an empty body is enough. Returns
        the list of agent×version rows; pass ``agent_id`` to keep only that agent's row.

        Example:
            >>> client = demo_client()
            >>> rows = client.agents.connector_install_status("cyops_utilities", "3.7.1")
            >>> rows[0].status
            'Completed'
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
        return [AgentConnectorStatus.model_validate(r) for r in rows]


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
