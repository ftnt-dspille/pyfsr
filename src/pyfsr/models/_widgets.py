"""Typed models for the widget upload + publish surface (``client.widgets``).

Covers the widget record shape used across upload (``POST
/api/3/solutionpacks/install?$type=widget``), the development-workspace
manifest (``GET /api/3/widgets/development/<uuid>``), and the live listing
(``GET /api/3/widgets``). Shapes are live-verified against an 8.0 appliance —
see ``docs/plans/WIDGET_UPLOAD_PUBLISH_PLAN.md`` for provenance.
"""

from __future__ import annotations

from pydantic import Field

from ._integration import ApiResult


class WidgetRecord(ApiResult):
    """A widget record from ``client.widgets`` (upload/publish/list/get).

    Dict-compatible, so ``record["uuid"]`` works alongside ``record.uuid``.
    Fields not modeled here (``tree``, layout metadata, ...) stay in ``extra``.
    """

    id_iri: str | None = Field(default=None, alias="@id")
    uuid: str | None = None
    name: str | None = None
    version: str | None = None
    title: str | None = None
    subTitle: str | None = None
    draft: bool | None = None
    installed: bool | None = None
    enablePublish: bool | None = None
    metadata: dict | None = None

    @property
    def published(self) -> bool:
        """True once the widget is live -- installed and no longer a draft."""
        return bool(self.installed) and not bool(self.draft)
