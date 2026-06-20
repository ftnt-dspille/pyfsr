"""Workflow-collection CRUD (``/api/3/workflow_collections``).

A *workflow collection* is the container FortiSOAR groups playbooks (workflows) under — the
top-level rows in **Automation → Playbooks**. This wraps the lifecycle operations so callers
(notably a playbook compiler/emitter) stop hand-rolling raw ``client.*`` calls and stepping on
the load-bearing gotchas below. Accessed as ``client.workflow_collections``.

Key behaviours that match the appliance (and differ from naive expectations):

- **create / update** POSTs or PUTs a **bare collection object** directly to
  ``/api/3/workflow_collections``. The nested ``workflows`` array is accepted inline.
- **upsert / bulk_upsert** hit the appliance's true re-push path and avoid the recycle-bin
  duplicate problem by restoring soft-deleted rows when needed.
- **delete** must send **no body** and ``$hardDelete=true&$showDeleted=true``. A ``{}`` body
  silently no-ops and leaks the collection.

Use :meth:`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_export` to replay a
FortiSOAR export file (the ``{"type": "workflow_collections", "data": [...]}`` envelope produced
by the UI's Export button) — it extracts the inner collection objects and posts each bare.
Pass ``replace=True`` to hard-delete any existing collection with the same uuid first.

Example::

    cols = client.workflow_collections.list()                 # all collections
    col  = client.workflow_collections.get("<uuid>")
    client.workflow_collections.create_collection("My Pack", workflows=[...])
    client.workflow_collections.upsert({...})                 # re-push one collection
    client.workflow_collections.create_collections([...])            # re-push many
    client.workflow_collections.import_export(data)           # replay an export dict
    client.workflow_collections.import_from_file("export.json")
    client.workflow_collections.update("<uuid>", name="Renamed")
    client.workflow_collections.delete("<uuid>")              # hard delete, no recycle bin
"""

from __future__ import annotations

import json
import re
import uuid as _uuid
from pathlib import Path
from typing import Any

from ..pagination import extract_members
from ..records import RecordSet
from .base import BaseAPI

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

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

    def create_collection(
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
        """Create a collection, optionally with workflows (``POST /api/3/workflow_collections``).

        Nested ``workflows`` (full Workflow objects with ``steps``/``routes``) are accepted
        inline. ``uuid`` is generated if omitted. Returns the created collection record.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("create_collection() requires a non-empty collection name")
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
        return self.client.post(_BASE, data=collection)

    def upsert(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert-or-update one collection via ``POST /api/3/upsert/workflow_collections``.

        FortiSOAR matches on the collection's natural key, restoring a soft-deleted row
        instead of creating a duplicate. This is the safest write path when a collection
        may already exist in the recycle bin.
        """
        return self.client.post("/api/3/upsert/workflow_collections", data=data)

    def create_collections(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Create or re-push many collections (``POST /api/3/bulkupsert/workflow_collections``)."""
        return self.client.post("/api/3/bulkupsert/workflow_collections", data=rows)

    # Keys present in FSR export payloads that must not be forwarded on import.
    # ``@context`` is the main offender: its presence tells the API layer this is an
    # existing-resource reference and routes the POST into an update path instead of a
    # create — producing a "null value in column name" constraint error. The audit
    # timestamps (``createDate``/``modifyDate``) are server-assigned and rejected on
    # write; the appliance ignores ``id``/``deletedAt``/``importedBy`` but we strip
    # them for cleanliness too.
    _STRIP_KEYS: frozenset[str] = frozenset({"@context", "createDate", "modifyDate", "deletedAt", "importedBy", "id"})

    @classmethod
    def _clean_item(cls, obj: Any) -> Any:
        """Recursively strip server-generated / Hydra-meta keys from an export payload."""
        if isinstance(obj, dict):
            return {k: cls._clean_item(v) for k, v in obj.items() if k not in cls._STRIP_KEYS}
        if isinstance(obj, list):
            return [cls._clean_item(i) for i in obj]
        return obj

    def import_export(
        self,
        data: dict[str, Any],
        *,
        replace: bool = False,
    ) -> list[dict[str, Any]]:
        """Import a FortiSOAR export envelope, preserving original UUIDs and structure.

        Accepts the ``{"type": "workflow_collections", "data": [...]}`` envelope produced
        by the UI's Export button. Each ``WorkflowCollection`` object in ``data["data"]``
        (with its nested ``workflows``) is posted as a bare object to
        ``POST /api/3/workflow_collections`` — mirroring the second call the UI makes
        during an import. Returns a list with one response dict per imported collection.

        ``replace=True`` hard-deletes any existing collection whose uuid matches an item
        in the export before re-importing it (the UI's "Replace existing playbook
        collection" flow: ``DELETE /api/3/workflow_collections/<uuid>?$hardDelete=true``
        then ``POST /api/3/workflow_collections``). Without ``replace=True`` the POST
        raises a ``409 UniqueConstraintViolationException`` if the collection already
        exists.

        Raises:
            ValueError: if ``data`` is not a dict or is missing the ``"data"`` key.

        Returns:
            List of created collection records (one per entry in ``data["data"]``).
        """
        if not isinstance(data, dict):
            raise ValueError("import_export() expects a dict (the export envelope)")
        if "data" not in data:
            raise ValueError(
                "import_export() expects an export envelope with a 'data' key; got keys: " + ", ".join(sorted(data))
            )
        results: list[dict[str, Any]] = []
        for raw_col in data["data"]:
            col = self._clean_item(raw_col)
            if replace:
                col_uuid = col.get("uuid")
                if col_uuid and self.exists(col_uuid):
                    self.delete(col_uuid)
            results.append(self.client.post(_BASE, data=col))
        return results

    def import_from_file(
        self,
        path: str | Path,
        *,
        replace: bool = False,
    ) -> list[dict[str, Any]]:
        """Load a FortiSOAR export JSON file and import it via :meth:`import_export`.

        ``path`` points to a ``*.json`` file produced by the UI's Export button. Pass
        ``replace=True`` to hard-delete any collection whose uuid already exists before
        re-creating it (the "Replace existing playbook collection" UI flow).

        Raises:
            FileNotFoundError: if ``path`` does not exist.
            ValueError: if the file is not valid JSON or lacks the ``"data"`` key.

        Returns:
            List of created collection records.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"export file not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"export file is not valid JSON: {path}: {exc}") from exc
        return self.import_export(data, replace=replace)

    def restore(self, uuid: str) -> dict[str, Any]:
        """Restore a soft-deleted collection from the recycle bin.

        This mirrors :meth:`pyfsr.records.RecordSet.restore` but keeps the collection-specific
        API self-contained.
        """
        return RecordSet(self.client, "workflow_collections", typed=False).restore(uuid)

    def exists(self, uuid: str) -> bool:
        """Return True if a collection with ``uuid`` exists on the appliance.

        Useful as a pre-flight check before :meth:`import_from_file` to avoid
        re-importing a collection that is already present.
        """
        if not isinstance(uuid, str) or not _UUID_RE.match(uuid.strip()):
            raise ValueError(f"exists() requires a valid uuid, got {uuid!r}")
        try:
            self.get(uuid.strip(), relationships=False)
            return True
        except Exception:
            return False

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
