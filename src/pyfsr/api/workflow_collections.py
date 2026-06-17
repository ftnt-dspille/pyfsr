"""Workflow-collection CRUD (``/api/3/workflow_collections``).

A *workflow collection* is the container FortiSOAR groups playbooks (workflows) under — the
top-level rows in **Automation → Playbooks**. This wraps the four lifecycle operations so
callers (notably a playbook compiler/emitter) stop hand-rolling raw ``client.*`` calls and
stepping on the load-bearing gotchas below. Accessed as ``client.workflow_collections``.

Two shapes are intentionally asymmetric, matching the appliance:

- **create** takes an *import-style envelope* (``{"type": ..., "data": [{...}]}``), the same
  payload the product's *Import* uses — not a bare record POST. :meth:`create` builds that
  envelope for you from a name + nested workflows.
- **delete** must send **no body** and ``$hardDelete=true&$showDeleted=true``. A ``{}`` body
  silently no-ops and leaks the collection; pyfsr's ``client.delete()`` already sends no body,
  and :meth:`delete` sets the params.

Example::

    cols = client.workflow_collections.list()                 # all collections
    col = client.workflow_collections.get("<uuid>")
    client.workflow_collections.create("My Pack", description="...", workflows=[...])
    client.workflow_collections.update("<uuid>", name="Renamed")
    client.workflow_collections.delete("<uuid>")              # hard delete, no recycle bin
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any

from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/workflow_collections"
# A hard delete must also reach already-recycled rows; together these skip the recycle bin.
_HARD_DELETE = {"$hardDelete": "true", "$showDeleted": "true"}


class WorkflowCollectionsAPI(BaseAPI):
    """CRUD for playbook (workflow) collections."""

    def list(self, *, limit: int = 2147483647, relationships: bool = False) -> list[dict[str, Any]]:
        """List workflow collections (the ``hydra:member`` array).

        ``relationships=True`` adds ``$relationships=true`` so each collection's nested
        ``workflows`` come back inline (heavier; off by default).
        """
        params: dict[str, Any] = {"$limit": limit}
        if relationships:
            params["$relationships"] = "true"
        return extract_members(self.client.get(_BASE, params=params))

    def get(self, uuid: str, *, relationships: bool = True) -> dict[str, Any]:
        """Fetch one collection by uuid. ``relationships=True`` (default) inlines its
        ``workflows`` — the usual reason to fetch a single collection."""
        uuid = _require_uuid(uuid, "get")
        params = {"$relationships": "true"} if relationships else None
        return self.client.get(f"{_BASE}/{uuid}", params=params)

    def create(
        self,
        name: str,
        *,
        description: str = "",
        visible: bool = True,
        workflows: list[dict[str, Any]] | None = None,
        uuid: str | None = None,
        record_tags: list[str] | None = None,
        image: str | None = None,
    ) -> dict[str, Any]:
        """Create a collection via the import-style envelope.

        ``workflows`` are full Workflow objects (each with its own ``steps``/``routes``) to
        nest under the collection — a standalone playbook is created by nesting it here, as
        there is no standalone ``POST /api/3/workflows``. ``uuid`` is generated if omitted.

        Returns the raw appliance response.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("create() requires a non-empty collection name")
        collection = {
            "@type": "WorkflowCollection",
            "name": name,
            "description": description,
            "visible": visible,
            "image": image,
            "uuid": uuid or str(_uuid.uuid4()),
            "recordTags": list(record_tags or []),
            "workflows": list(workflows or []),
        }
        envelope = {
            "type": "workflow_collections",
            "macros": [],
            "exported_tags": [],
            "data": [collection],
        }
        return self.client.post(_BASE, data=envelope)

    def update(self, uuid: str, **fields: Any) -> dict[str, Any]:
        """Partially update a collection (``PUT``); pass only the keys you want changed
        (e.g. ``name=...``, ``visible=False``, ``description=...``)."""
        uuid = _require_uuid(uuid, "update")
        if not fields:
            raise ValueError("update() requires at least one field to change")
        return self.client.put(f"{_BASE}/{uuid}", data=fields)

    def delete(self, uuid: str, *, hard: bool = True) -> None:
        """Delete a collection. ``hard=True`` (default) bypasses the recycle bin.

        Sends **no request body** — the appliance silently no-ops a delete with a ``{}``
        body and leaks the collection, so this never passes one. ``hard=False`` does a soft
        (recycle-bin) delete.
        """
        uuid = _require_uuid(uuid, "delete")
        params = dict(_HARD_DELETE) if hard else None
        self.client.delete(f"{_BASE}/{uuid}", params=params)


def _require_uuid(uuid: str, op: str) -> str:
    if not isinstance(uuid, str) or not uuid.strip():
        raise ValueError(f"{op}() requires a non-empty collection uuid")
    return uuid.strip()
