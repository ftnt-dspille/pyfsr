"""Role CRUD and module-permission management (``/api/3/roles``).

A FortiSOAR **role** bundles a set of ``ModulePermission`` records (one per module) that
together define what a user assigned that role can read, write, create, delete, and execute.
This API wraps the lifecycle operations that matter most for automation:

- **list / get** — discover roles by name or uuid.
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

import re
from typing import Any

from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/roles"
_MODULE_PERMS = "/api/3/module_permissions"
_MODULES = "/api/3/modules"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Keys that FSR assigns server-side and rejects (or misroutes) on write.
# Stripping these from existing perms before re-PUTting prevents accidental corruption.
_PERM_STRIP = frozenset({"@context", "@id", "id", "createDate", "modifyDate", "deletedAt", "uuid"})


def _is_uuid(s: str) -> bool:
    return bool(_UUID_RE.match(s.strip()))


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
    _role_cache: dict[str, dict[str, Any]] | None = None  # name -> record
    _module_cache: dict[str, dict[str, Any]] | None = None  # type -> record

    def _role_by_name(self) -> dict[str, dict[str, Any]]:
        if self._role_cache is None:
            self._role_cache = {r["name"]: r for r in self.list() if r.get("name")}
        return self._role_cache

    def _module_by_type(self) -> dict[str, dict[str, Any]]:
        if self._module_cache is None:
            resp = self.client.get(_MODULES, params={"$limit": 2147483647})
            self._module_cache = {
                m["type"]: m for m in (resp or {}).get("hydra:member", []) if m.get("type")
            }
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

    # ------------------------------------------------------------------- read
    def list(self, *, limit: int = 2147483647) -> list[dict[str, Any]]:
        """List all roles (``GET /api/3/roles``)."""
        return extract_members(self.client.get(_BASE, params={"$limit": limit}))

    def get(self, role: str, *, relationships: bool = False) -> dict[str, Any]:
        """Fetch one role by uuid or name.

        ``relationships=True`` inlines ``modulePermissions`` — useful for inspecting what
        a role currently has before modifying it.
        """
        uuid = self._resolve_role_uuid(role)
        params = {"$relationships": "true"} if relationships else None
        return self.client.get(f"{_BASE}/{uuid}", params=params)

    def module_permissions(self, role: str) -> list[dict[str, Any]]:
        """Return the ``ModulePermission`` records currently assigned to ``role``.

        ``role`` may be a uuid or friendly name.
        """
        record = self.get(role, relationships=True)
        return record.get("modulePermissions") or []

    # ------------------------------------------------------------------ write
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
    ) -> dict[str, Any]:
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

        existing = self.module_permissions(role_uuid)
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

        return self.client.put(f"{_BASE}/{role_uuid}", data={"modulePermissions": merged})
