from __future__ import annotations

from typing import Any

from .base import BaseAPI


def _module_from_iri(iri: str) -> str:
    parts = [p for p in iri.split("/") if p]
    if len(parts) >= 2:
        return parts[-2]
    raise ValueError(f"Cannot derive module from record IRI: {iri!r}")


class RecordModuleAPI(BaseAPI):
    """Shared CRUD surface for first-class record modules (tasks, incidents, ...).

    Mirrors the :class:`~pyfsr.api.alerts.AlertsAPI` pattern: friendly picklist
    values are resolved to IRIs on create/update, and ``create`` accepts a
    ``record`` link to attach the new record to a parent (e.g. a task linked to
    its alert). Subclasses set :attr:`module`.
    """

    module: str = ""

    def create(
        self,
        *,
        resolve_picklists: bool = True,
        record: str | list[str] | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        """Create a record, optionally linked to a parent record.

        Args:
            resolve_picklists: When True (default), friendly picklist values are
                mapped to IRIs before sending.
            record: Parent record IRI (or list) to link via the parent module's
                relationship field (derived from the IRI).
            **data: Record fields (e.g. ``name``, ``status``).
        """
        if record is not None:
            iris = [record] if isinstance(record, str) else list(record)
            modules = {_module_from_iri(iri) for iri in iris}
            if len(modules) != 1:
                raise ValueError(f"All linked records must share one module, got: {sorted(modules)}")
            data[modules.pop()] = iris
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data)
        return self.client.post(f"/api/3/{self.module}", data=data)

    def list(self, params: dict | None = None) -> dict[str, Any]:
        """List records, optionally filtered via query parameters."""
        return self.client.get(f"/api/3/{self.module}", params=params)

    def get(self, record_id: str) -> dict[str, Any]:
        """Get a single record by ID."""
        return self.client.get(f"/api/3/{self.module}/{record_id}")

    def update(self, record_id: str, data: dict[str, Any], *, resolve_picklists: bool = True) -> dict[str, Any]:
        """Update a record; friendly picklist values are resolved unless disabled."""
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data)
        return self.client.put(f"/api/3/{self.module}/{record_id}", data=data)

    def delete(self, record_id: str) -> None:
        """Delete a record."""
        self.client.delete(f"/api/3/{self.module}/{record_id}")
