"""Dedicated-tenant multitenancy: run a remote FortiSOAR node as a tenant of this master.

A *dedicated tenant* is a remote FortiSOAR node that this master manages. Setting one up is a
two-sided dance that the product's UI performs with a handful of calls; this module wraps them
so the same steps can be scripted. Accessed as ``client.multitenancy``.

**Master side** (the box that owns the tenant):

1. :meth:`MultitenancyAPI.add_dedicated_tenant` creates the tenant record and its master-side
   agent record (``isDedicated`` tenant + ``agentId`` = the tenant node's 32-hex ``tenantId``).
2. :meth:`MultitenancyAPI.wait_for_health` polls the agent until the remote node connects
   (``Remote Node Connected``).
3. :meth:`MultitenancyAPI.export_config` returns the bundle the tenant needs (address, ports,
   router and agent credentials, ``agentId`` = this master's id).
4. :meth:`MultitenancyAPI.encryption_key` returns the key the tenant must be configured with;
   it is the agent record's ``encryptionKey`` and changes whenever that record is re-created.
5. :meth:`MultitenancyAPI.delete_tenant` removes the tenant (and its agent record with it).

**Tenant side** (the remote node being attached):

6. :meth:`MultitenancyAPI.configure_master` creates the ``Master`` router and the ``Master``
   agent from the exported config and the key from step 4.
7. :meth:`MultitenancyAPI.status` shows the ``Master`` agent's ``configstatus``
   (``Connection Verified`` once the link works).

**Idempotency.** The product's UI is not idempotent: when the tenant-side agent create fails
after the router was created, a retry collides with the leftover router. Router and agent uuids
here are derived from the master's ``agentId``, so a leftover is found by uuid and reported as
a :class:`MultitenancyError` with ``leftover_router`` set. Pass ``replace_leftover=True`` to
delete the leftover and configure again.

**Secrets.** :meth:`MultitenancyAPI.export_config` returns an :class:`ExportedConfig` whose
``repr``/``str`` mask the password, certificate and key fields. Returned agent/tenant records
have the secret fields removed. Nothing here logs secret values.

Example:
    >>> client = demo_client()
    >>> tenant, agent = client.multitenancy.add_dedicated_tenant("branch-7")
    >>> client.multitenancy.wait_for_health("branch-7", "Remote Node Connected", timeout=600)
    'Remote Node Connected'
    >>> config = client.multitenancy.export_config("branch-7")
    >>> key = client.multitenancy.encryption_key("branch-7")
    >>> # on the tenant box (its own client):
    >>> tenant_client.multitenancy.configure_master(config, key)  # doctest: +SKIP
    >>> client.multitenancy.delete_tenant("branch-7")
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from ..exceptions import FortiSOARException, ResourceNotFoundError
from ..models._agents import Agent
from ..models._multitenancy import Tenant, TenantStatus
from ..pagination import extract_members
from .agents import _as_router_iri, resolve_agent_id
from .api_keys import DEFAULT_TEAM
from .base import BaseAPI

_logger = logging.getLogger(__name__)

_TENANTS = "/api/3/tenants"
_BULK_DELETE_TENANTS = "/api/3/delete/tenants"
_AGENTS = "/api/3/agents"
_ROUTERS = "/api/3/routers"
_AGENT_INSTALLER = "/api/integration/agent-installer/?format=json"
_ALL = 2147483647
_TENANT_ID = re.compile(r"^[0-9a-f]{32}$")

#: Record name the tenant side gives both its router and its agent.
MASTER_NAME = "Master"
#: API port of the tenant's Master router (the UI's value).
MASTER_API_PORT = 15671
#: Picklist values the UI sends; resolved by name against the live picklists.
DEFAULT_AGENT_TYPE = "FSR Node"
DEFAULT_AUTH_TYPE = "Basic Auth"
DEFAULT_CONFIGSTATUS = "Configured"
#: The UI saves the tenant-side Master *router* as "Not Configured" (the agent carries the real status).
ROUTER_CONFIGSTATUS = "Not Configured"
#: The tenant-side agent's ``role``: a plain string, not a picklist.
MASTER_ROLE = "master"

#: Export/record fields that carry credentials. Masked in :class:`ExportedConfig` reprs and
#: stripped from the records this module returns.
SECRET_FIELDS = frozenset({"password", "clientKey", "clientCert", "certificate", "encryptionKey"})

#: Keys :meth:`MultitenancyAPI.configure_master` needs from the master's export.
_REQUIRED_CONFIG = (
    "agentId",
    "address",
    "tcpPort",
    "sni",
    "routerUsername",
    "password",
    "certificate",
    "username",
    "vhost",
    "authType",
)


class MultitenancyError(FortiSOARException):
    """A dedicated-tenant step failed.

    ``leftover_router`` is set when an earlier configure attempt left its ``Master`` router
    behind (the uuid of that router); re-run with ``replace_leftover=True`` to clear it.
    """

    def __init__(
        self,
        message: str,
        response: Any = None,
        *,
        error_type: str | None = None,
        leftover_router: str | None = None,
    ) -> None:
        self.leftover_router = leftover_router
        super().__init__(message, response, error_type=error_type)


def mask_secrets(data: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``data`` with every non-empty secret field replaced by ``'***'``."""
    return {k: ("***" if k in SECRET_FIELDS and v else v) for k, v in data.items()}


class ExportedConfig(dict):
    """The parsed master export (:meth:`MultitenancyAPI.export_config`).

    A plain ``dict`` for every purpose except display: ``repr`` and ``str`` mask the password,
    certificate and key fields so a logged or printed config does not leak them.
    """

    def __repr__(self) -> str:
        return f"ExportedConfig({mask_secrets(self)!r})"

    __str__ = __repr__


def _scrub(record: Any) -> dict[str, Any]:
    """A dict copy of a server record without its secret fields."""
    if not isinstance(record, dict):
        return {}
    return {k: v for k, v in record.items() if k not in SECRET_FIELDS}


def _status(exc: BaseException) -> int | None:
    return getattr(exc, "status_code", None)


#: The uuids the UI gives the tenant-side ``Master`` router and agent. They are the same on every tenant
#: and master (live-verified on 8.0.1): the tenant's routing agent only connects when the records carry
#: them -- records with any other uuid are accepted but never connect (``ACCESS_REFUSED``).
MASTER_ROUTER_UUID = "d134a192-2a3e-4bd9-a4b9-11db811803f6"
MASTER_AGENT_UUID = "cd6ec33c-c4f7-42d7-8ab1-3934df772669"


def _master_uuid(agent_id: str, kind: str) -> str:
    """The fixed uuid of the tenant-side ``kind`` (``router``/``agent``) ``Master`` record."""
    return MASTER_ROUTER_UUID if kind == "router" else MASTER_AGENT_UUID


def _require_text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} requires a non-empty string")
    return value.strip()


class MultitenancyAPI(BaseAPI):
    """Dedicated-tenant setup, health, config export and teardown (``client.multitenancy``)."""

    # ------------------------------------------------------------ master side
    def add_dedicated_tenant(
        self,
        name: str,
        tenant_id: str,
        *,
        sme: str | Mapping[str, Any] | None = None,
        owners: Sequence[Any] | None = None,
        version: str | None = None,
    ) -> tuple[Tenant, Agent]:
        """Create a dedicated tenant and its master-side agent record; return ``(tenant, agent)``.

        Two calls, as the UI makes them: ``POST /api/3/tenants`` (``isDedicated``, the
        ``tenantId``, ``owners``) and ``POST /api/3/agents`` (``agentId`` = the same ``tenantId``,
        ``version``, ``agentType`` = ``"FSR Node"``, ``authType`` = ``"Basic Auth"``, the router,
        and the tenant IRI). Picklist values are resolved by name, not hard-coded uuids.

        ``tenant_id`` is the 32-hex ``tenantId`` of the tenant node's own ``Self`` tenant. It is required:
        the master cannot know it (read it on the tenant node). ``sme`` is the SME router
        (record, IRI or uuid); default is :meth:`~pyfsr.api.routers.RoutersAPI.first`.
        ``owners`` are team names/IRIs (default ``"SOC Team"``, the team the message router can
        read). ``version`` defaults to the master's build version without its build number.

        A failure on the agent call leaves the tenant behind; :meth:`delete_tenant` removes it.

        Example:
            >>> client = demo_client()
            >>> tenant, agent = client.multitenancy.add_dedicated_tenant("branch-7")
            >>> tenant.name
            'branch-7'
        """
        name = _require_text(name, "add_dedicated_tenant()")
        if not isinstance(tenant_id, str) or not re.fullmatch(r"[0-9a-f]{32}", tenant_id):
            raise ValueError("tenant_id must be the TENANT node's own Self tenantId (32 hex)")
        tenant_ref = tenant_id
        if self._tenant_by_name(name) is not None:
            raise MultitenancyError(f"a tenant named {name!r} already exists; delete it or pick another name")

        owner_objects = self._owner_objects(owners)
        router_iri = self._router_iri(sme)
        master_version = version or self._master_version()
        agent_type = self._pick("agents", "agentType", DEFAULT_AGENT_TYPE).rsplit("/", 1)[-1]  # bare uuid, as the UI
        auth_type = self._picklist_object(self._pick("agents", "authType", DEFAULT_AUTH_TYPE))

        try:
            tenant_rec = self.client.post(
                _TENANTS,
                data={"name": name, "isDedicated": True, "tenantId": tenant_ref, "owners": owner_objects},
            )
        except FortiSOARException as exc:
            if _status(exc) == 409:
                raise MultitenancyError(f"tenant {name!r} already exists (409): {exc}", exc.response) from exc
            raise
        tenant_iri = (tenant_rec or {}).get("@id") or (self._tenant_by_name(name) or {}).get("@id")

        try:
            agent_rec = self.client.post(
                _AGENTS,
                data={
                    "name": name,
                    "agentId": tenant_ref,
                    "version": master_version,
                    "owners": owner_objects,
                    "agentType": agent_type,
                    "router": router_iri,
                    "authType": auth_type,
                    "tenants": tenant_iri,  # a single IRI, as the UI sends it
                },
            )
        except FortiSOARException as exc:
            raise MultitenancyError(
                f"tenant {name!r} was created but its agent was not ({exc}); "
                f"run delete_tenant({name!r}) before retrying",
                exc.response,
            ) from exc
        _logger.info("multitenancy: added dedicated tenant %r (agent awaiting the remote node)", name)
        return Tenant.model_validate(tenant_rec or {}), Agent.model_validate(_scrub(agent_rec))

    def add_shared_tenant(
        self,
        name: str,
        *,
        owners: Sequence[Any] | None = None,
    ) -> Tenant:
        """Create a shared tenant on this node and return it.

        A shared tenant lives on the same node as the data it segregates (no separate tenant node,
        no agent, no router): ``POST /api/3/tenants`` with ``isDedicated: false`` and the owner
        teams, which is what Multi Tenancy > Tenants > Add > Shared sends. Records are then
        assigned to the tenant, and the owner teams see only their tenant's records.

        ``owners`` are team names/IRIs (default ``"SOC Team"``). Create the team first with
        ``client.teams.get_or_create``. Raises :class:`MultitenancyError` when a tenant with that
        name already exists.

        Example:
            >>> client = demo_client()
            >>> client.multitenancy.add_shared_tenant("FortiGate", owners=["FortiGate Team"]).name
            'FortiGate'
        """
        name = _require_text(name, "add_shared_tenant()")
        if self._tenant_by_name(name) is not None:
            raise MultitenancyError(f"a tenant named {name!r} already exists; delete it or pick another name")
        owner_objects = self._owner_objects(owners)
        try:
            rec = self.client.post(_TENANTS, data={"name": name, "isDedicated": False, "owners": owner_objects})
        except FortiSOARException as exc:
            if _status(exc) == 409:
                raise MultitenancyError(f"tenant {name!r} already exists (409): {exc}", exc.response) from exc
            raise
        _logger.info("multitenancy: added shared tenant %r", name)
        return Tenant.model_validate(rec or {})

    def wait_for_health(
        self,
        name: str,
        state: str,
        *,
        timeout: float = 300.0,
        interval: float = 5.0,
    ) -> str:
        """Poll agent ``name`` until its ``configurationHealth`` equals ``state``; return it.

        The health walks ``Configuration In Progress`` -> ``Awaiting Remote Node Connection``
        -> ``Remote Node Connected``. Raises :class:`MultitenancyError` after ``timeout``
        seconds, naming the state it was last in.

        Example:
            >>> client = demo_client()
            >>> client.multitenancy.wait_for_health("branch-7", "Remote Node Connected", timeout=600)
            'Remote Node Connected'
        """
        _require_text(name, "wait_for_health()")
        deadline = time.monotonic() + timeout
        while True:
            current = self._picklist_value(self._require_agent(name).get("configurationHealth"))
            if current == state:
                return current
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MultitenancyError(
                    f"agent {name!r} is at {current!r} after {timeout:g}s; still waiting for {state!r}"
                )
            time.sleep(max(0.0, min(interval, remaining)))

    def export_config(self, name_or_agent_id: str) -> ExportedConfig:
        """Export the config a tenant needs for ``name_or_agent_id`` (its name, ``agentId`` or uuid).

        ``POST /api/integration/agent-installer/?format=json`` with ``{"agent": <agentId>}``.
        The result has ``address``, ``agentId`` (this master's id), ``authType``,
        ``certificate``, ``clientCert``, ``clientKey``, ``isDedicated``, ``password``,
        ``routerUsername``, ``selfId``, ``sni``, ``tcpPort``, ``username``, ``vhost``. It is an
        :class:`ExportedConfig`, which masks secrets when printed.

        Example:
            >>> client = demo_client()
            >>> config = client.multitenancy.export_config("branch-7")
            >>> config["agentId"]  # doctest: +SKIP
        """
        ref = _require_text(name_or_agent_id, "export_config()")
        agent_id = resolve_agent_id(self.client, ref)
        resp = self.client.post(_AGENT_INSTALLER, data={"agent": agent_id})
        if not isinstance(resp, dict):
            raise MultitenancyError("agent-installer returned no config object for the dedicated tenant")
        return ExportedConfig(resp)

    def encryption_key(self, name: str) -> str:
        """The ``encryptionKey`` of agent ``name``'s master record (149 characters on 8.0.1).

        It changes whenever that agent record is re-created, so fetch it again after any
        re-creation and give the tenant the new one.

        Example:
            >>> client = demo_client()
            >>> key = client.multitenancy.encryption_key("branch-7")
            >>> len(key) > 0
            True
        """
        rec = self._require_agent(_require_text(name, "encryption_key()"))
        key = rec.get("encryptionKey")
        if not key and rec.get("uuid"):
            full = self._get_or_none(f"{_AGENTS}/{rec['uuid']}") or {}
            key = full.get("encryptionKey")
        if not key:
            raise MultitenancyError(
                f"agent {name!r} has no encryptionKey; only a dedicated tenant's master record has one"
            )
        return str(key)

    def delete_tenant(self, name: str) -> None:
        """Delete dedicated tenant ``name`` and its agent record.

        ``DELETE /api/3/delete/tenants`` with ``{"ids": [<uuid>]}``, the call the UI makes, then
        ``DELETE /api/3/agents/<uuid>`` for the agent of the same name if it is still there. If the
        tenant is still listed afterwards (API-key auth turns a bulk delete into a no-op) it is deleted
        per row.

        Example:
            >>> client = demo_client()
            >>> client.multitenancy.delete_tenant("branch-7")
        """
        name = _require_text(name, "delete_tenant()")
        tenant = self._tenant_by_name(name)
        if tenant is None or not tenant.get("uuid"):
            raise MultitenancyError(f"no tenant named {name!r}")
        # The UI deletes through the bulk route; it also tears down the tenant's message-exchange vhost,
        # which the per-row DELETE leaves behind. Under API-key auth the client decomposes a bulk delete
        # into per-row calls keyed on ``uuid`` and an ``{"ids": [...]}`` body deletes nothing, so verify
        # the tenant is gone and fall back to the per-row delete.
        self.client.request("DELETE", _BULK_DELETE_TENANTS, data={"ids": [tenant["uuid"]]})
        if self._tenant_by_name(name) is not None:
            self.client.delete(f"{_TENANTS}/{tenant['uuid']}")
        agent = self._agent_by_name(name)
        if agent is not None and agent.get("uuid"):
            try:
                self.client.delete(f"{_AGENTS}/{agent['uuid']}")
            except FortiSOARException:
                # The master removes the agent itself shortly after the tenant goes (live-verified
                # on 8.0.1), so this delete can 404 or 500 when it races that cleanup. Only fail
                # if the agent is still there afterwards.
                if self._agent_by_name(name) is not None:
                    raise

    # ------------------------------------------------------------- tenant side
    def configure_master(
        self,
        config: Mapping[str, Any],
        encryption_key: str,
        *,
        owners: Sequence[Any] | None = None,
        role: str | None = None,
        tenants: Sequence[Any] | None = None,
        replace_leftover: bool = False,
    ) -> Agent:
        """Attach this node to its master: create the ``Master`` router, then the ``Master`` agent.

        Run on the tenant box. ``config`` is the master's :meth:`export_config` result (a plain
        mapping works too); ``encryption_key`` is :meth:`encryption_key` from the master.

        1. ``POST /api/3/routers`` with the router uuid (derived from ``config["agentId"]``),
           ``apiPort`` 15671, the address/sni/ports/credentials from ``config``, the key, and
           ``configstatus``.
        2. ``POST /api/3/agents?$relationships=true&$asJson=true`` for the ``Master`` agent, with
           the same key, the router IRI, ``owners`` (default ``"SOC Team"``), ``authType`` from
           ``config``, and ``agentType`` ``"FSR Node"``. ``role`` and ``tenants`` are sent only
           when given.

        Before anything is written, a leftover from an earlier attempt (router or agent with the
        derived uuid) raises :class:`MultitenancyError` with ``leftover_router`` set. Pass
        ``replace_leftover=True`` to delete it first. A decrypt failure (``DB Password decrypt
        failed``, the key does not match the master) or a uuid conflict on the agent step also
        raises :class:`MultitenancyError`, and the router is left behind for
        ``replace_leftover=True`` on the retry.

        Example:
            >>> tenant = demo_client()  # the tenant box's client
            >>> config = {"agentId": "0123456789abcdef0123456789abcdef", "address": "master.example.com",
            ...           "tcpPort": 443, "sni": "master.example.com", "routerUsername": "router",
            ...           "password": "<redacted>", "certificate": "<pem>", "username": "agent",
            ...           "vhost": "/", "authType": "Basic Auth"}
            >>> tenant.multitenancy.configure_master(config, "<encryption-key>")  # doctest: +SKIP
        """
        cfg = dict(config)
        missing = [k for k in _REQUIRED_CONFIG if cfg.get(k) in (None, "")]
        if missing:
            raise ValueError(f"configure_master(): config is missing {', '.join(missing)} (use export_config())")
        key = _require_text(encryption_key, "configure_master() encryption_key")
        agent_id = str(cfg["agentId"])
        router_uuid = _master_uuid(agent_id, "router")
        agent_uuid = _master_uuid(agent_id, "agent")

        # Resolve everything that can fail on input before touching the box.
        owner_objects = self._owner_objects(owners)
        router_configstatus = self._pick("routers", "configstatus", ROUTER_CONFIGSTATUS)
        agent_configstatus = self._pick("agents", "configstatus", DEFAULT_CONFIGSTATUS)
        agent_type = self._pick("agents", "agentType", DEFAULT_AGENT_TYPE)
        auth_type = self._pick("agents", "authType", str(cfg["authType"]))
        role_value = role if role is not None else MASTER_ROLE

        leftovers = []
        if self._get_or_none(f"{_AGENTS}/{agent_uuid}") is not None:
            leftovers.append((_AGENTS, agent_uuid))
        if self._get_or_none(f"{_ROUTERS}/{router_uuid}") is not None:
            leftovers.append((_ROUTERS, router_uuid))
        # Master records made another way (the UI gives them random uuids) are invisible to the
        # uuid lookups above and to the list endpoint; find them by name through the query API.
        for path in (_AGENTS, _ROUTERS, _TENANTS):
            for rec in self._query_by_name(path, MASTER_NAME):
                if rec.get("uuid") and (path, rec["uuid"]) not in leftovers:
                    leftovers.append((path, rec["uuid"]))
        if leftovers and not replace_leftover:
            kinds = {_AGENTS: "agent", _ROUTERS: "router", _TENANTS: "tenant"}
            found = ", ".join(f"{kinds[path]} {uid}" for path, uid in leftovers)
            raise MultitenancyError(
                f"a Master record from an earlier attempt already exists on this box ({found}). "
                "Re-run with replace_leftover=True to delete it and configure again.",
                leftover_router=next((uid for path, uid in leftovers if path == _ROUTERS), None),
            )
        # Agents first (they point at the router and the Master tenant), then routers, then tenants.
        for path, uid in sorted(leftovers, key=lambda leftover: (_AGENTS, _ROUTERS, _TENANTS).index(leftover[0])):
            self.client.delete(f"{path}/{uid}")

        router_body = {
            "uuid": router_uuid,
            "name": MASTER_NAME,
            "apiPort": MASTER_API_PORT,
            "tcpPort": cfg["tcpPort"],
            "address": cfg["address"],
            "sni": cfg["sni"],
            "username": cfg["routerUsername"],
            "password": cfg["password"],
            "certificate": cfg["certificate"],
            "encryptionKey": key,
            "configstatus": router_configstatus,
        }
        try:
            self.client.post(_ROUTERS, data=router_body)
        except FortiSOARException as exc:
            if _status(exc) == 409:
                raise MultitenancyError(
                    f"router uuid {router_uuid} already exists (409 UniqueConstraint(uuid)); "
                    "re-run with replace_leftover=True",
                    exc.response,
                    leftover_router=router_uuid,
                ) from exc
            raise

        agent_body: dict[str, Any] = {
            "uuid": agent_uuid,
            "agentId": agent_id,
            "agentType": agent_type,
            "name": MASTER_NAME,
            "active": True,
            "vhost": cfg["vhost"],
            "ssl": True,
            "isLocal": False,
            "username": cfg["username"],
            "password": cfg["password"],
            "configstatus": agent_configstatus,
            "router": router_uuid,
            "owners": owner_objects,
            "authType": auth_type,
            "encryptionKey": key,
        }
        agent_body["role"] = role_value
        # The UI creates the "Master" tenant record inline with the agent, inactive, and then
        # activates it. Without that tenant the master's messages never reach this node.
        agent_body["tenants"] = (
            list(tenants)
            if tenants is not None
            else [
                {
                    "tenantId": agent_id,
                    "active": False,
                    "isDedicated": True,
                    "name": MASTER_NAME,
                    "allowRemoteMMDModification": True,
                    "role": MASTER_ROLE,
                    "owners": owner_objects,
                }
            ]
        )
        try:
            agent_rec = self.client.post(f"{_AGENTS}?$relationships=true&$asJson=true", data=agent_body)
        except FortiSOARException as exc:
            detail = str(exc.message or exc)
            left = f"the Master router {router_uuid} from this attempt was left behind"
            if "decrypt" in detail.lower():
                msg = (
                    f"the encryption key does not decrypt the master's password ({detail}). {left}; "
                    "fix the key and re-run with replace_leftover=True"
                )
            elif _status(exc) == 409:
                msg = (
                    f"agent uuid {agent_uuid} already exists (409 UniqueConstraint(uuid)). "
                    f"{left}; re-run with replace_leftover=True"
                )
            else:
                msg = f"Master agent create failed ({detail}). {left}; re-run with replace_leftover=True"
            raise MultitenancyError(msg, exc.response, leftover_router=router_uuid) from exc
        for tenant_rec in self._query_by_name(_TENANTS, MASTER_NAME):
            if tenant_rec.get("uuid") and tenant_rec.get("role") == MASTER_ROLE:
                self.client.put(f"{_TENANTS}/{tenant_rec['uuid']}", data={"active": True})
        _logger.info("multitenancy: configured the Master router and agent for master agent %s", agent_id)
        return Agent.model_validate(_scrub(agent_rec))

    def remove_master(self) -> list[str]:
        """Detach this node from its master: delete its ``Master`` agent, router and tenant; return their uuids.

        Run on the tenant box, after :meth:`delete_tenant` on the master. Finds the records by name
        through the query API (the list endpoint hides them, and a UI-made record has a random
        uuid), deleting agents before routers. Returns ``[]`` when the node has none.

        Example:
            >>> tenant = demo_client()  # the tenant box's client
            >>> tenant.multitenancy.remove_master()  # doctest: +SKIP
            []
        """
        removed: list[str] = []
        for path in (_AGENTS, _ROUTERS, _TENANTS):
            for rec in self._query_by_name(path, MASTER_NAME):
                if rec.get("uuid"):
                    self.client.delete(f"{path}/{rec['uuid']}")
                    removed.append(str(rec["uuid"]))
        return removed

    def status(self, name: str) -> TenantStatus:
        """Health of agent ``name`` on this box: ``configurationHealth`` and ``configstatus``.

        On the master, ``name`` is the dedicated tenant's name (health walks to ``Remote Node
        Connected``). On the tenant box, ``name`` is ``"Master"`` (``configstatus`` reaches
        ``Connection Verified`` once the link works).

        Example:
            >>> client = demo_client()
            >>> client.multitenancy.status("branch-7").configurationHealth  # doctest: +SKIP
        """
        rec = self._require_agent(_require_text(name, "status()"))
        return TenantStatus(
            name=rec.get("name"),
            uuid=rec.get("uuid"),
            agentId=rec.get("agentId"),
            active=rec.get("active"),
            configurationHealth=self._picklist_value(rec.get("configurationHealth")),
            configstatus=self._picklist_value(rec.get("configstatus")),
        )

    # ------------------------------------------------- master -> tenant playbooks
    def tenant_id(self, name: str) -> str:
        """The 32-hex node id (``agentId``) of dedicated tenant ``name``, as the push and
        Trigger Tenant Playbook calls expect it.

        Example:
            >>> client = demo_client()
            >>> client.multitenancy.tenant_id("branch-7")  # doctest: +SKIP
            '7e83db46a87b2ab7e06432c034e423c8'
        """
        return str(self._require_agent(_require_text(name, "tenant_id()"))["agentId"])

    def push_playbooks(
        self,
        collections: Sequence[str],
        tenants: Sequence[str],
        *,
        replace: bool = True,
    ) -> dict[str, Any]:
        """Push playbook collections from this master to dedicated tenants.

        The same call the UI's Tenant Manager > Push Playbooks makes. Each tenant gets a copy
        with the same collection and playbook uuids. Every playbook pushed with
        ``remoteExecutableFlag`` set also gets a playbook alias, named after the playbook and
        mapped to each tenant (see :meth:`playbook_alias`).

        Args:
            collections: Collection uuids or exact names on this master.
            tenants: Tenant names (resolved with :meth:`tenant_id`) or 32-hex tenant ids.
            replace: Replace a collection of the same name on the tenant (the UI default).

        Example:
            >>> client = demo_client()
            >>> client.multitenancy.push_playbooks(["MT Health - Tenant"], ["branch-7"])  # doctest: +SKIP
        """
        if not collections or not tenants:
            raise ValueError("push_playbooks() needs at least one collection and one tenant")
        coll_ids = [self._collection_uuid(c) for c in collections]
        tenant_ids = [t.lower() if _TENANT_ID.match(str(t).lower()) else self.tenant_id(t) for t in tenants]
        return self.client.post(
            "/api/postman/playbook-push/",
            params={"format": "json"},
            data={"collectionIds": coll_ids, "tenants": tenant_ids, "replace": bool(replace)},
        )

    def playbook_alias(self, name: str) -> dict[str, Any]:
        """The playbook alias named ``name``: ``{"uuid", "name", "tenants": [32-hex ids]}``.

        ``uuid`` is what a Trigger Tenant Playbook step takes as ``playbook_alias_id``;
        ``tenants`` lists the tenants the alias is mapped on.

        Example:
            >>> client = demo_client()
            >>> client.multitenancy.playbook_alias("MT Health Marker")["uuid"]  # doctest: +SKIP
        """
        name = _require_text(name, "playbook_alias()")
        resp = self.client.get("/api/postman/wf-alias/", params={"format": "json"})
        hits = [a for a in (resp or {}).get("results", []) if isinstance(a, dict) and a.get("name") == name]
        if not hits:
            raise MultitenancyError(f"no playbook alias named {name!r}; push its collection first")
        if len(hits) > 1:
            raise MultitenancyError(f"{len(hits)} playbook aliases are named {name!r}")
        a = hits[0]
        return {
            "uuid": a.get("uuid"),
            "name": a.get("name"),
            "tenants": [w.get("tenant_id") for w in a.get("remote_wfs") or [] if isinstance(w, dict)],
        }

    def _collection_uuid(self, ref: str) -> str:
        ref = _require_text(ref, "collection")
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", ref.lower()):
            return ref.lower()
        hits = [
            c
            for c in extract_members(
                self.client.get("/api/3/workflow_collections", params={"name": ref, "$limit": _ALL})
            )
            if isinstance(c, dict) and c.get("name") == ref
        ]
        if len(hits) != 1:
            raise MultitenancyError(f"{len(hits)} playbook collections are named {ref!r}")
        return str(hits[0]["uuid"])

    # ----------------------------------------------------------------- helpers
    def _tenant_id_or_self(self, tenant_id: str | None) -> str:
        if tenant_id is None:
            for t in extract_members(self.client.get(_TENANTS, params={"$limit": _ALL})):
                if isinstance(t, dict) and (t.get("role") == "self" or t.get("name") == "Self") and t.get("tenantId"):
                    tenant_id = t["tenantId"]
                    break
            else:
                raise MultitenancyError("no 'Self' tenant with a tenantId on this master; pass tenant_id=")
        tenant_id = str(tenant_id).strip().lower()
        if not _TENANT_ID.match(tenant_id):
            raise ValueError("tenant_id must be the 32-hex tenantId of the tenant node's Self tenant")
        return tenant_id

    def _tenant_by_name(self, name: str) -> dict[str, Any] | None:
        matches = [
            t
            for t in extract_members(self.client.get(_TENANTS, params={"$limit": _ALL}))
            if isinstance(t, dict) and t.get("name") == name
        ]
        if len(matches) > 1:
            raise MultitenancyError(f"{len(matches)} tenants are named {name!r}")
        return matches[0] if matches else None

    def _agent_by_name(self, name: str) -> dict[str, Any] | None:
        matches = [
            a
            for a in extract_members(self.client.get(_AGENTS, params={"$limit": _ALL}))
            if isinstance(a, dict) and a.get("name") == name
        ]
        if not matches:
            # A tenant node's list endpoint hides its own Master records (live-verified on 8.0.1:
            # the list is empty while the box is registered and healthy); the query API shows them.
            matches = self._query_by_name(_AGENTS, name)
        if len(matches) > 1:
            raise MultitenancyError(f"{len(matches)} agents are named {name!r}; use the agent's agentId instead")
        return matches[0] if matches else None

    def _query_by_name(self, path: str, name: str) -> list[dict[str, Any]]:
        """Records of module ``path`` named ``name``, read through ``/api/query``.

        Filtered client-side: the query API silently ignores some server-side filters.
        """
        module = path.rsplit("/", 1)[-1]
        resp = self.client.post(f"/api/query/{module}", data={"sort": [], "limit": _ALL, "logic": "AND", "filters": []})
        return [r for r in extract_members(resp) if isinstance(r, dict) and r.get("name") == name]

    def _require_agent(self, name: str) -> dict[str, Any]:
        rec = self._agent_by_name(name)
        if rec is None:
            raise MultitenancyError(f"no agent named {name!r}")
        return rec

    def _get_or_none(self, path: str) -> dict[str, Any] | None:
        try:
            resp = self.client.get(path)
        except ResourceNotFoundError:
            return None
        return resp if isinstance(resp, dict) and resp else None

    def _owner_iris(self, owners: Sequence[Any] | None) -> list[str]:
        names = [DEFAULT_TEAM] if owners is None else list(owners)
        return self.client.teams._resolve_teams(names)

    def _owner_objects(self, owners: Sequence[Any] | None) -> list[dict[str, Any]]:
        """Owner teams as the full objects the UI sends on the tenant-side Master records."""
        names = [DEFAULT_TEAM] if owners is None else list(owners)
        objects = []
        for resolved in self.client.teams._resolve_teams(names):
            uuid = str(resolved).rsplit("/", 1)[-1]
            team = self.client.teams.get(uuid)
            objects.append(
                {
                    "@id": f"/api/3/teams/{uuid}",
                    "@type": "Team",
                    "description": team.description,
                    "name": team.name,
                    "uuid": uuid,
                    "importedBy": [],
                }
            )
        return objects

    def _picklist_object(self, iri: str) -> dict[str, Any]:
        """A picklist item as the full object the UI sends (the record plus ``display``)."""
        item = self.client.get(f"/api/3/picklists/{iri.rsplit('/', 1)[-1]}")
        return {**item, "display": item.get("itemValue")}

    def _router_iri(self, sme: str | Mapping[str, Any] | None) -> str:
        if sme is None:
            sme = self.client.routers.first()
            if sme is None:
                raise MultitenancyError(
                    "this master has no SME router; enable it with `csadm secure-message-exchange enable`"
                )
        return _as_router_iri(sme)

    def _master_version(self) -> str:
        info = self.client.version()
        raw = info.get("version") if isinstance(info, dict) else info
        if not raw:
            raise MultitenancyError("could not read this master's version; pass version=")
        return str(raw).split("-", 1)[0]

    def _pick(self, module: str, field: str, value: str) -> str:
        """The picklist IRI for friendly ``value`` on ``module.field`` (IRIs pass through).

        A field that is not a picklist sends ``value`` unchanged.
        """
        if value.startswith("/api/"):
            return value
        picklists = self.client.picklists
        picklist = picklists.for_field(module, field)
        if not picklist:
            return value
        iri = picklists.resolve(value, picklist=picklist)
        if iri is None:
            valid = picklists.options(picklist)
            raise MultitenancyError(
                f"{value!r} is not a {module}.{field} value. Valid ({len(valid)}): {', '.join(valid) or '(none)'}"
            )
        return iri

    def _picklist_value(self, value: Any) -> str | None:
        """The friendly ``itemValue`` of a picklist field read as an expanded dict, IRI or string."""
        if value is None:
            return None
        if isinstance(value, dict):
            return value.get("itemValue")
        if isinstance(value, str) and value.startswith("/api/"):
            info = self.client.picklists.reverse_resolve(value)
            return info.get("itemValue") if info else value
        return str(value)
