"""Application navigation and module-visibility configuration (``/api/views/1/app``).

The navigation editor and module-visibility endpoint controls which modules appear in
the navigation bar and how their visibility is gated (by module + permission requirement,
or unrestricted). Each navigation item carries a ``require`` field — either an empty
array ``[]`` (no visibility restriction) or an object ``{"module": "...", "action": "..."}``
that gates visibility by whether the calling user has the named permission on the named
module.

**Visibility vs Installation:** The ``require`` field gates visibility by *permission*
(e.g. ``canRead`` on ``sla_templates``), **not** by whether the solution pack is
installed. A module may appear in navigation because the permission action evaluates
true, even though the pack is absent — creating a discrepancy between the UI option set
(which lists it) and the actual module catalog (which does not).

**Issue 1290662 Context:** SLA Templates module appears in the navigation editor's
option list even when the SLA Templates solution pack is not installed, because the
visibility rule gates on permission, not installation status.

Accessed as ``client.app_config``.

Example:
    .. code-block:: python

        # Fetch the current navigation config
        config = client.app_config.get()
        nav = config["config"]["navigation"]

        # Find the SLA Templates entry (if present)
        sla_item = client.app_config.find_navigation_item(module="sla_templates")
        print(sla_item["require"])  # {"module": "sla_templates", "action": "canRead"}

        # Update navigation items
        updated = client.app_config.update_navigation([...updated items...])
"""

from __future__ import annotations

from typing import Any

from .base import BaseAPI

#: Fixed endpoint for application configuration (navigation, module visibility).
_APP_CONFIG = "/api/views/1/app"


class AppConfigAPI(BaseAPI):
    """Manage application navigation structure and module visibility gating.

    The navigation bar and module-visibility configuration is stored as a single
    document at ``GET|PUT /api/views/1/app``. Each navigation item's ``require``
    field controls visibility: an empty array means unrestricted, while an object
    with ``module`` and ``action`` keys restricts visibility to users with that
    permission on that module.

    Use these methods to inspect or modify navigation items and their visibility
    constraints.
    """

    def get(self) -> dict[str, Any]:
        """Fetch the current application configuration (navigation + visibility).

        Returns:
            The full application configuration document:
            ``{"id": "app", "type": "app", "config": {"header": {...}, "navigation": [...]}}``

        Example:
            .. code-block:: python

                config = client.app_config.get()
                nav_items = config["config"]["navigation"]
        """
        result = self.client.get(_APP_CONFIG)
        assert isinstance(result, dict)
        return result

    def get_navigation(self) -> list[dict[str, Any]]:
        """Return just the ``navigation`` array from the application config.

        The navigation array is a list of navigation items; each item has
        ``title``, ``icon``, ``require``, ``state``, and other fields that
        define a module's presence in the navigation bar.

        Returns:
            List of navigation items (the ``config.navigation`` array).

        Example:
            .. code-block:: python

                nav = client.app_config.get_navigation()
                print(f"Navigation has {len(nav)} items")
        """
        config = self.get()
        nav = config.get("config", {}).get("navigation", [])
        assert isinstance(nav, list)
        return nav

    def find_navigation_item(
        self, module: str | None = None, title: str | None = None, nav: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """Find a single navigation item by module name or title.

        Searches the current navigation for an item matching the given criteria.
        Returns the first match, or ``None`` if no item is found.

        Args:
            module: Module name to match (e.g. ``"sla_templates"``). Matched
                against the item's ``state.parameters.module`` field.
            title: Human-readable title to match (e.g. ``"SLA Templates"``).
                Matched against the item's ``title`` field (case-sensitive).
            nav: Optional pre-fetched navigation array. If not provided, will call
                :meth:`get_navigation` to fetch it. Internal use.

        Returns:
            The matching navigation item dict (with ``title``, ``icon``, ``require``,
            ``state``, etc.), or ``None`` if not found.

        Raises:
            ValueError: if both ``module`` and ``title`` are ``None``.

        Example:
            .. code-block:: python

                # Find SLA Templates by module name
                item = client.app_config.find_navigation_item(module="sla_templates")
                if item:
                    print(item["title"], item["require"])

                # Find by title instead
                alerts_item = client.app_config.find_navigation_item(title="Alerts")
        """
        if module is None and title is None:
            raise ValueError("find_navigation_item() requires module or title")

        if nav is None:
            nav = self.get_navigation()
        return self._search_nav(nav, module, title)

    @classmethod
    def _search_nav(cls, nav: list[dict[str, Any]], module: str | None, title: str | None) -> dict[str, Any] | None:
        """Depth-first search of the (nested) navigation tree.

        Top-level entries are menu *groups* carrying their leaf entries under
        ``items``; only the leaves bind a module. A leaf's module may live on
        ``require.module`` (the visibility gate) or ``state.parameters.module``
        (the route param) — and ``state``/``parameters`` are frequently ``None``
        or an empty list rather than a dict, so every hop is type-guarded.
        """
        for item in nav:
            if not isinstance(item, dict):
                continue
            if title is not None and item.get("title") == title:
                return item
            if module is not None and cls._item_module(item) == module:
                return item
            children = item.get("items")
            if isinstance(children, list):
                hit = cls._search_nav(children, module, title)
                if hit is not None:
                    return hit
        return None

    @staticmethod
    def _item_module(item: dict[str, Any]) -> str | None:
        """Extract the module a nav leaf binds, from ``require`` or ``state``."""
        require = item.get("require")
        if isinstance(require, dict) and require.get("module"):
            return require["module"]
        state = item.get("state")
        params = state.get("parameters") if isinstance(state, dict) else None
        if isinstance(params, dict):
            return params.get("module")
        return None

    def update_navigation(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace the entire ``navigation`` array and commit via PUT.

        Updates the navigation configuration by replacing the ``navigation`` array
        with the provided items, then PUTs the entire configuration back to the
        appliance.

        This is a full replacement, not a merge: the entire navigation array is
        substituted. To make a surgical change to one item, use
        :meth:`find_navigation_item` to read it, modify it, then collect all
        items (unchanged + modified) and pass them here.

        Args:
            items: New list of navigation items. Each item should have at minimum
                ``title``, ``icon``, ``require``, and ``state`` fields. ``require``
                may be an empty list ``[]`` (unrestricted) or an object
                ``{"module": "...", "action": "..."}`` (gated by permission).

        Returns:
            The API response (the updated configuration document).

        Raises:
            ValueError: if ``items`` is not a list or is empty.

        Example:
            .. code-block:: python

                # Read current navigation, modify one item, save
                nav = client.app_config.get_navigation()
                sla_item = next((x for x in nav if x["title"] == "SLA Templates"), None)
                if sla_item:
                    sla_item["require"] = []  # Make unrestricted
                client.app_config.update_navigation(nav)
        """
        if not isinstance(items, list):
            raise ValueError("items must be a list of navigation objects")
        if not items:
            raise ValueError("items list cannot be empty")

        config = self.get()
        config["config"]["navigation"] = items
        result = self.client.put(_APP_CONFIG, data=config)
        assert isinstance(result, dict)
        return result

    def set_navigation_visibility(
        self, module: str, *, require: list[Any] | dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Update the visibility gate (``require``) for a single navigation item.

        Finds the navigation item for the given module, updates its ``require``
        field, and commits the change. This is a convenience wrapper around
        :meth:`find_navigation_item` + :meth:`update_navigation`.

        Args:
            module: Module name (e.g. ``"sla_templates"``).
            require: Visibility gate. Options:
                - ``None`` or ``[]``: unrestricted visibility.
                - ``{"module": "...", "action": "..."}```: gated by permission
                  on a specific module.

        Returns:
            The API response (the updated configuration document).

        Raises:
            ValueError: if no navigation item is found for the module.

        Example:
            .. code-block:: python

                # Make SLA Templates unrestricted
                client.app_config.set_navigation_visibility("sla_templates", require=[])

                # Gate visibility by canRead permission on sla_templates module
                client.app_config.set_navigation_visibility(
                    "sla_templates",
                    require={"module": "sla_templates", "action": "canRead"}
                )
        """
        nav = self.get_navigation()
        item = self.find_navigation_item(module=module, nav=nav)
        if item is None:
            raise ValueError(f"No navigation item found for module {module!r}")

        # Update the item's require field (note: modifies item in-place in nav)
        if require is None:
            item["require"] = []
        else:
            item["require"] = require

        return self.update_navigation(nav)
