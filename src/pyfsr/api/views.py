from __future__ import annotations

from typing import Any

from .base import BaseAPI

#: Fixed canonical viewset segment. ``/api/views/1/...`` is the only routed
#: viewset (``0`` and ``2`` 404); the active layouts all hang off it.
_VIEWSET = 1

#: The three layout kinds a module exposes as system view templates.
_KINDS = ("list", "detail", "form")


class ViewsAPI(BaseAPI):
    """Resolve a module's **active** system view template (SVT) layout.

    A module can carry several SVT rows for the same layout — duplicates, both
    flagged ``isDefault: true`` — so the live layout must never be picked by name
    or flag. ``GET /api/views/1/modules-<module>-<kind>`` resolves the single
    active template the platform actually renders; that is what these methods
    return. To enumerate (or write) the raw SVT rows instead, use
    ``client.modules_admin.get_view_templates(module)``.

    Example:
        .. code-block:: python

            svt = client.views.detail("alerts")
            print(svt["uuid"], svt["type"])  # active detail layout

            # Walk the tabs of the detail layout to place a widget, etc.
            tabs = svt["config"].get("tabs", [])
    """

    def __init__(self, client):
        super().__init__(client)

    def resolve(self, module: str, kind: str = "detail") -> dict[str, Any]:
        """Return the active SVT for ``module`` and layout ``kind``.

        Args:
            module: Module name (e.g. ``"alerts"``).
            kind: One of ``"list"``, ``"detail"`` (default), or ``"form"``.

        Returns:
            The resolved active system view template (a ``type: "rows"`` layout
            for list/detail, ``type: "form"`` for form), including its ``uuid``,
            ``config``, ``module``, and ``isDefault``.

        Raises:
            ValueError: if ``kind`` is not one of the three layout kinds.
        """
        if kind not in _KINDS:
            raise ValueError(f"kind must be one of {_KINDS}, got {kind!r}")
        result = self.client.get(f"/api/views/{_VIEWSET}/modules-{module}-{kind}")
        assert isinstance(result, dict)
        return result

    def detail(self, module: str) -> dict[str, Any]:
        """Return the active **detail** (View Panel) layout SVT for ``module``."""
        return self.resolve(module, "detail")

    def listing(self, module: str) -> dict[str, Any]:
        """Return the active **list** (grid) layout SVT for ``module``."""
        return self.resolve(module, "list")

    def form(self, module: str) -> dict[str, Any]:
        """Return the active **form** (add/edit) layout SVT for ``module``."""
        return self.resolve(module, "form")
