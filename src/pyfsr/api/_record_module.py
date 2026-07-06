from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..utils.iri import module_from_iri as _module_from_iri
from .base import BaseAPI


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
        strict_picklists: bool = False,
        validate: bool = False,
        record: str | list[str] | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        """Create a record, optionally linked to a parent record.

        Args:
            resolve_picklists: When True (default), friendly picklist values are
                mapped to IRIs before sending.
            strict_picklists: When True, raise
                :class:`~pyfsr.exceptions.PicklistResolutionError` *before* the
                POST when a friendly value doesn't resolve (names the field, bad
                value, and valid options, instead of an opaque box 400). Default
                False leaves unresolvable values in place.
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
            data = self.client.picklists.resolve_record_fields(self.module, data, strict=strict_picklists)
        return self.client.post(f"/api/3/{self.module}", data=data)

    def list(self, params: dict | None = None) -> dict[str, Any]:
        """List records, optionally filtered via query parameters.

        .. note::

            Returns the raw hydra envelope (``{"hydra:member": [...], ...}``).
            Prefer the modern :meth:`client.records(module)
            <pyfsr.client.FortiSOAR.records>` surface, which unpacks the envelope,
            returns typed (dict-compatible) records, and offers ``.first()`` /
            ``.list()`` / iteration.
        """
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
        strict_picklists: bool = False,
        validate: bool = False,
    ) -> dict[str, Any]:
        """Update a record; friendly picklist values are resolved unless disabled.

        Args:
            record_id: The record UUID or IRI to update.
            data: Fields to update (e.g. ``{"status": "Closed"}``).
            resolve_picklists: When True (default), friendly picklist values are
                mapped to IRIs before sending.
            strict_picklists: When True, raise pre-flight on an unresolvable
                picklist value (see :meth:`create`).
            validate: When True, run client-side validation (field types, etc.)
                before PUT. Raises ValidationError on failure.
                Default False for backward compatibility.
        """
        if validate:
            self._validate_record(data)
        if resolve_picklists:
            data = self.client.picklists.resolve_record_fields(self.module, data, strict=strict_picklists)
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
        """Client-side validation against the module's typed model.

        Looks up the curated Pydantic model for :attr:`module` in
        ``MODEL_REGISTRY`` and validates ``data`` against it. Because every
        record model uses ``extra="allow"`` and types its fields as optional,
        this type-checks the *known* fields (e.g. rejecting an ``int`` for a
        ``str``-typed field) while leaving unknown keys and partial updates
        untouched — so it is safe for both ``create`` and ``update``.

        Modules without a curated model (parsed as the bare ``BaseRecord``) have
        no typed fields to check, so validation is a no-op for them.

        Raises:
            ValidationError: When ``data`` fails the typed-model validation.
        """
        from pydantic import ValidationError as PydanticValidationError

        from ..exceptions import ValidationError
        from ..models import BaseRecord, model_for

        model = model_for(self.module)
        if model is BaseRecord:
            # No curated model for this module — nothing typed to validate.
            return
        try:
            model.model_validate(data)
        except PydanticValidationError as exc:
            problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors())
            raise ValidationError(f"validation failed for {self.module}: {problems}") from exc
