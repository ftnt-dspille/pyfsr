from __future__ import annotations

from collections.abc import Iterable
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
        validate: bool = False,
        record: str | list[str] | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        """Create a record, optionally linked to a parent record.

        Args:
            resolve_picklists: When True (default), friendly picklist values are
                mapped to IRIs before sending.
            validate: When True, run client-side validation (required fields,
                field types) before POST. Raises ValidationError on failure.
                Default False for backward compatibility.
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
        if validate:
            self._validate_record(data)
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data)
        return self.client.post(f"/api/3/{self.module}", data=data)

    def list(self, params: dict | None = None) -> dict[str, Any]:
        """List records, optionally filtered via query parameters."""
        return self.client.get(f"/api/3/{self.module}", params=params)

    def get(self, record_id: str) -> dict[str, Any]:
        """Get a single record by ID."""
        return self.client.get(f"/api/3/{self.module}/{record_id}")

    def update(
        self,
        record_id: str,
        data: dict[str, Any],
        *,
        resolve_picklists: bool = True,
        validate: bool = False,
    ) -> dict[str, Any]:
        """Update a record; friendly picklist values are resolved unless disabled.

        Args:
            record_id: The record UUID or IRI to update.
            data: Fields to update (e.g. ``{"status": "Closed"}``).
            resolve_picklists: When True (default), friendly picklist values are
                mapped to IRIs before sending.
            validate: When True, run client-side validation (field types, etc.)
                before PUT. Raises ValidationError on failure.
                Default False for backward compatibility.
        """
        if validate:
            self._validate_record(data)
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data)
        return self.client.put(f"/api/3/{self.module}/{record_id}", data=data)

    def delete(self, record_id: str) -> None:
        """Delete a record."""
        self.client.delete(f"/api/3/{self.module}/{record_id}")

    def resolve_bulk(
        self,
        records: Iterable[dict[str, Any]],
        *,
        strict: bool = False,
    ) -> list[dict[str, Any]]:
        """Batch resolve picklist values across multiple records in one pass.

        Reuses the existing picklist resolution logic via
        :meth:`~pyfsr.api.picklists.PicklistsAPI.resolve_record_fields`, applying
        it to each record. More efficient than calling ``resolve_record_fields``
        in a loop since the picklist caches warm up once and are reused.

        Args:
            records: An iterable of record dicts to resolve (e.g. from a query).
            strict: When True, raise :class:`~pyfsr.exceptions.PicklistResolutionError`
                on a picklist miss. When False (default), leaves unresolved values
                in place (compatible with per-record resolution behavior).

        Returns:
            A list of resolved records (dicts), in the same order as input.
        """
        out = []
        for record in records:
            resolved = self.client.picklists.resolve_record_fields(
                self.module,
                record,
                strict=strict,
            )
            out.append(resolved)
        return out

    def _validate_record(self, data: dict[str, Any]) -> None:
        """Minimal client-side validation: field presence and type checks.

        Currently validates:
          - No validation performed (reserved for future pydantic model integration).

        If a typed model becomes available via the models registry, this will
        validate against it. For now, this is a placeholder that allows the
        ``validate=`` parameter to be used without error, maintaining the API
        contract for future extensions.

        Raises:
            ValidationError: When validation fails.
        """
        # Placeholder: validation framework reserved for model-based typing
        # (currently all modules use dict[str, Any] internally).
        # When pydantic models are wired up, this will invoke model validation.
        pass
