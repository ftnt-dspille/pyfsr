"""Export templates (``/api/3/export_templates``).

An export template names a reusable selection of modules / picklists / records to
include in a configuration export. This wrapper covers the basic CRUD so callers
stop hand-posting to the raw endpoint. Accessed as ``client.export_templates``.

Example::

    tmpl = client.export_templates.create(
        "Nightly alerts",
        options={"modules": ["alerts"], "picklistNames": ["/api/3/picklist_names/alert-severity"]},
    )
"""

from __future__ import annotations

from typing import Any

from ..models import ExportTemplate
from ..pagination import extract_members
from ..projection import iri_to_uuid
from .base import BaseAPI

_BASE = "/api/3/export_templates"


class ExportTemplatesAPI(BaseAPI):
    """Create, list, fetch, and delete export templates."""

    def create(self, name: str, *, options: dict[str, Any] | None = None, **fields: Any) -> ExportTemplate:
        """Create an export template.

        ``options`` is the selection payload (e.g.
        ``{"modules": ["alerts"], "picklistNames": [...]}``). Extra ``fields`` are
        merged into the body. Returns a typed :class:`~pyfsr.models.ExportTemplate`.
        """
        payload: dict[str, Any] = {"name": name}
        if options is not None:
            payload["options"] = options
        payload.update(fields)
        return ExportTemplate.model_validate(self.client.post(_BASE, data=payload))

    def list(self, params: dict[str, Any] | None = None) -> list[ExportTemplate]:
        """List export templates as typed :class:`~pyfsr.models.ExportTemplate` records."""
        return [ExportTemplate.model_validate(m) for m in extract_members(self.client.get(_BASE, params=params))]

    def get(self, ref: str) -> ExportTemplate:
        """Fetch an export template by uuid or IRI (typed)."""
        return ExportTemplate.model_validate(self.client.get(f"{_BASE}/{iri_to_uuid(ref)}"))

    def find_by_name(self, name: str) -> ExportTemplate | None:
        """Return the export template named ``name``, or ``None`` if absent."""
        for tmpl in self.list(params={"name": name}):
            if tmpl.name == name:
                return tmpl
        return None

    def get_or_create(
        self, name: str, *, options: dict[str, Any] | None = None, **fields: Any
    ) -> tuple[ExportTemplate, bool]:
        """Idempotently ensure an export template ``name`` exists; return ``(template, created)``.

        If a template with that name already exists, it is returned unchanged (its
        ``options``/``fields`` are **not** modified). Returns ``created=True`` only
        when the template was newly created.
        """
        existing = self.find_by_name(name)
        if existing is not None:
            return existing, False
        return self.create(name, options=options, **fields), True

    def delete(self, ref: str) -> None:
        """Delete an export template by uuid or IRI."""
        self.client.delete(f"{_BASE}/{iri_to_uuid(ref)}")
