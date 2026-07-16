"""The reporting module — ``client.reporting``.

Read the report definitions behind the SOAR UI's *Reports* section
(``/api/3/reporting``).

**The display name is ``displayName``, not ``name``** — there is no ``name`` field
on a :class:`~pyfsr.models.Report`, so lookups here match on ``displayName``. That
is the name the export wizard's *Reports* category shows and the one
``ExportTemplate.add_report()`` takes.
"""

from __future__ import annotations

from typing import Any

from ..models import Report
from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/reporting"


class ReportingAPI(BaseAPI):
    """List and resolve report definitions (``/api/3/reporting``).

    Example:
        .. code-block:: python

            for report in client.reporting.list():
                print(report.displayName, report.templateType)

            soc = client.reporting.get("SOC Summary Report")
    """

    def list(self, params: dict[str, Any] | None = None, *, typed: bool = True) -> list[Report] | list[dict[str, Any]]:
        """List report definitions.

        Args:
            params: optional query params passed through to the collection.
            typed: parse into :class:`~pyfsr.models.Report` (default) or return dicts.
        """
        members = [m for m in extract_members(self.client.get(_BASE, params=params)) if isinstance(m, dict)]
        if not typed:
            return members
        return [Report.model_validate(m) for m in members]

    def get(self, name: str, *, typed: bool = True) -> Report | dict[str, Any]:
        """Resolve a single report by its **display name**.

        Args:
            name: the report's ``displayName`` (e.g. ``"SOC Summary Report"``).
            typed: parse into :class:`~pyfsr.models.Report` (default) or return a dict.

        Raises:
            ValueError: if no report carries that display name.
        """
        members = extract_members(self.client.get(_BASE, params={"displayName": name}))
        for record in members:
            if isinstance(record, dict):
                return Report.model_validate(record) if typed else record
        raise ValueError(f"report {name!r} not found")
