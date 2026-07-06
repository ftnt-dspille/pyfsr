"""Role CRUD and module-permission management (``/api/3/roles``).

A FortiSOAR **role** bundles a set of ``ModulePermission`` records (one per module) that
together define what a user assigned that role can read, write, create, delete, and execute.
This API wraps the lifecycle operations that matter most for automation:

- **list / get / create** — discover roles by name or uuid, and create new ones.
- **grant_module_permissions** — the primary write operation. Module-level permissions
  live on ``/api/3/module_permissions`` but that collection is GET-only; the only write
  path is ``PUT /api/3/roles/{uuid}`` with a ``modulePermissions`` array. This method
  handles resolution (module type → IRI, role name → uuid) and merges new permissions
  with any that already exist on the role.
- **module_permissions** — list the ``ModulePermission`` records currently assigned to
  one role.

Typical use after creating a custom module::

    # Grant all CRUD+execute on a new module to all roles that need it
    client.roles.grant_module_permissions(
        "Full App Permissions",
        module="oguraly_test_processes",
    )

    # Or scope to specific permissions
    client.roles.grant_module_permissions(
        "SOC Analyst",
        module="oguraly_test_processes",
        can_read=True, can_create=False, can_update=False,
        can_delete=False, can_execute=False,
    )
"""

from __future__ import annotations

from typing import Any

from ..models import ModulePermission, Role
from ..pagination import extract_members
from ..utils.validation import is_uuid as _is_uuid
from .base import BaseAPI

_BASE = "/api/3/roles"
_MODULE_PERMS = "/api/3/module_permissions"
_MODULES = "/api/3/modules"

# Keys that FSR assigns server-side and rejects (or misroutes) on write.
# Stripping these from existing perms before re-PUTting prevents accidental corruption.
_PERM_STRIP = frozenset({"@context", "@id", "id", "createDate", "modifyDate", "deletedAt", "uuid"})


def _clean_perm(perm: dict[str, Any]) -> dict[str, Any]:
    """Return a write-safe copy of a ModulePermission record.

    Strips server-assigned keys and normalises the ``module`` field to a bare IRI string
    so FSR accepts the object on PUT without treating it as an existing-resource reference.
    """
    cleaned = {k: v for k, v in perm.items() if k not in _PERM_STRIP}
    # module comes back as {"@id": "/api/3/modules/<uuid>", ...} — collapse to IRI string
    mod = cleaned.get("module")
    if isinstance(mod, dict):
        cleaned["module"] = mod.get("@id", mod)
    # fieldPermissions may contain server keys too; keep only the IRI if present
    fp = cleaned.get("fieldPermissions")
    if isinstance(fp, list):
        cleaned["fieldPermissions"] = [(f.get("@id") if isinstance(f, dict) else f) for f in fp]
    return cleaned


class RolesAPI(BaseAPI):
    """Role discovery and module-permission management."""

    # ------------------------------------------------------------------ cache
    _role_cache: dict[str, Role] | None = None  # name -> record
    _module_cache: dict[str, dict[str, Any]] | None = None  # type -> record

    def _role_by_name(self) -> dict[str, Role]:
        if self._role_cache is None:
            self._role_cache = {r["name"]: r for r in self.list() if r.get("name")}
        return self._role_cache

    def _module_by_type(self) -> dict[str, dict[str, Any]]:
        if self._module_cache is None:
            resp = self.client.get(_MODULES, params={"$limit": 2147483647})
            self._module_cache = {m["type"]: m for m in (resp or {}).get("hydra:member", []) if m.get("type")}
        return self._module_cache

    def _resolve_role_uuid(self, role: str) -> str:
        """Accept a role uuid or name and return the uuid."""
        if _is_uuid(role):
            return role.strip()
        record = self._role_by_name().get(role)
        if not record:
            raise ValueError(f"role {role!r} not found; call list() to see available roles")
        return record["uuid"]

    def _resolve_module_iri(self, module: str) -> str:
        """Accept a module type (e.g. ``'alerts'``) and return its ``/api/3/modules/<uuid>`` IRI."""
        if module.startswith("/api/"):
            return module
        rec = self._module_by_type().get(module)
        if not rec:
            raise ValueError(
                f"module {module!r} not found in /api/3/modules; "
                "it may not be published yet — call modules_admin.publish() first"
            )
        return rec["@id"]

    # ------------------------------------------------------ name/uuid resolution
    def role_map(self) -> dict[str, str]:
        """Return ``{name: uuid}`` for all roles, cached for the instance lifetime."""
        return {name: r["uuid"] for name, r in self._role_by_name().items()}

    def role_uuid_by_name(self, name: str) -> str | None:
        """Look up a role UUID by display name (case-sensitive); ``None`` if absent."""
        return self.role_map().get(name)

    def _resolve_roles(self, roles: list[str]) -> list[str]:
        """Accept role UUIDs or names; return UUIDs. Raises ``ValueError`` for unknown names."""
        return [self._resolve_role_uuid(r) for r in roles]

    # ------------------------------------------------------------------- read
    def list(self, *, limit: int = 2147483647, params: dict[str, Any] | None = None) -> list[Role]:
        """List all roles (``GET /api/3/roles``) as typed :class:`~pyfsr.models.Role` records.

        ``params`` adds/overrides query params (e.g. ``{"$page": 1}``); ``$limit``
        defaults to ``limit`` unless ``params`` supplies one.
        """
        query = dict(params or {})
        query.setdefault("$limit", limit)
        return [Role.model_validate(m) for m in extract_members(self.client.get(_BASE, params=query))]

    def get(self, role: str, *, relationships: bool = False) -> Role:
        """Fetch one role by uuid or name as a :class:`~pyfsr.models.Role`.

        ``relationships=True`` inlines ``modulePermissions`` (parsed into
        :class:`~pyfsr.models.ModulePermission`) — useful for inspecting what
        a role currently has before modifying it.
        """
        uuid = self._resolve_role_uuid(role)
        params = {"$relationships": "true"} if relationships else None
        return Role.model_validate(self.client.get(f"{_BASE}/{uuid}", params=params))

    def module_permissions(self, role: str) -> list[ModulePermission]:
        """Return the :class:`~pyfsr.models.ModulePermission` records on ``role``.

        ``role`` may be a uuid or friendly name. Equivalent to
        ``roles.get(role, relationships=True).modulePermissions`` but returns
        ``[]`` (not ``None``) when the role has none.
        """
        return self.get(role, relationships=True).modulePermissions or []

    def _module_permissions_raw(self, role: str) -> list[dict[str, Any]]:
        """Raw (untyped) ``modulePermissions`` dicts for ``role``.

        Used internally by :meth:`grant_module_permissions` where the records are
        re-PUT and must stay as plain dicts — :meth:`module_permissions` returns
        typed ``ModulePermission`` models, which :func:`_clean_perm` can't walk
        (it expects ``.items()`` / ``isinstance(.., dict)``).
        """
        uuid = self._resolve_role_uuid(role)
        record = self.client.get(f"{_BASE}/{uuid}", params={"$relationships": "true"})
        return record.get("modulePermissions") or []

    # ------------------------------------------------------------------ write
    def create(self, name: str, *, description: str | None = None) -> Role:
        """Create a role (``POST /api/3/roles``).

        Only ``name`` is required; ``description`` is optional. The returned
        :class:`~pyfsr.models.Role` starts with an empty ``modulePermissions``
        list — use :meth:`grant_module_permissions` to attach module grants.

        Args:
            name: Role name (must be unique).
            description: Optional role description.
        """
        body: dict[str, Any] = {"name": name}
        if description is not None:
            body["description"] = description
        return Role.model_validate(self.client.post(_BASE, data=body))

    def grant_module_permissions(
        self,
        role: str,
        *,
        module: str,
        can_read: bool = True,
        can_create: bool = True,
        can_update: bool = True,
        can_delete: bool = True,
        can_execute: bool = True,
    ) -> Role:
        """Add or replace module-level permissions on a role.

        Resolves ``role`` (uuid or name) and ``module`` (module type like ``'alerts'`` or
        a full ``/api/3/modules/<uuid>`` IRI), then PUTs the role with a merged
        ``modulePermissions`` array — the only write path the FSR API exposes
        (``POST /api/3/module_permissions`` is GET-only).

        If the role already has a permission record for ``module``, it is replaced;
        otherwise the new record is appended. Returns the updated role record.

        Args:
            role: Role uuid or friendly name (e.g. ``"Full App Permissions"``).
            module: Module type (e.g. ``"oguraly_test_processes"``) or full module IRI.
            can_read: Read permission flag (defaults to ``True``).
            can_create: Create permission flag (defaults to ``True``).
            can_update: Update permission flag (defaults to ``True``).
            can_delete: Delete permission flag (defaults to ``True``).
            can_execute: Execute permission flag (defaults to ``True``).
        """
        role_uuid = self._resolve_role_uuid(role)
        module_iri = self._resolve_module_iri(module)

        existing = self._module_permissions_raw(role_uuid)
        new_perm: dict[str, Any] = {
            "@type": "ModulePermission",
            "module": module_iri,
            "canRead": can_read,
            "canCreate": can_create,
            "canUpdate": can_update,
            "canDelete": can_delete,
            "canExecute": can_execute,
            "fieldPermissions": [],
        }

        # Replace the existing record for this module (if any), else append.
        # Existing perms are cleaned before re-PUT to strip server-side keys
        # (@context, @id, uuid, timestamps) that FSR rejects or misroutes on write.
        merged: list[dict[str, Any]] = []
        replaced = False
        for perm in existing:
            perm_mod = perm.get("module") or {}
            existing_iri = perm_mod.get("@id") if isinstance(perm_mod, dict) else perm_mod
            if existing_iri == module_iri:
                merged.append(new_perm)
                replaced = True
            else:
                merged.append(_clean_perm(perm))
        if not replaced:
            merged.append(new_perm)

        return Role.model_validate(self.client.put(f"{_BASE}/{role_uuid}", data={"modulePermissions": merged}))
