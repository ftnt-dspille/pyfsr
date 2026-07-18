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

The navigation tree is modelled as :class:`~pyfsr.models.NavItem` throughout: reads
return typed items, and writes accept them. Construct entries with the typed models
and the SDK serializes them to the wire shape::

    from pyfsr.models import NavItem, NavRequire, NavState

    leaf = NavItem(
        title="My Module",
        icon="icon icon-bookmark",
        state=NavState(name="main.modules.list", parameters={"module": "my_module"}),
        require=NavRequire(module="my_module", action="read"),
    )
    client.app_config.add_navigation_item(leaf, parent="Incident Response", position="top")
"""

from __future__ import annotations

from typing import Any, Literal

from ..models import NavItem, NavRequire, NavState
from .base import BaseAPI

#: Fixed endpoint for application configuration (navigation, module visibility).
_APP_CONFIG = "/api/views/1/app"


def _item_module(item: NavItem) -> str | None:
    """Extract the module a nav leaf binds, from ``require`` or ``state``.

    A leaf's module may live on ``require.module`` (the visibility gate) or on
    ``state.parameters.module`` (the route param); ``parameters`` is sometimes an
    empty list rather than a dict, so the state hop is type-guarded.
    """
    require = item.require
    if isinstance(require, NavRequire) and require.module:
        return require.module
    params = item.state.parameters if isinstance(item.state, NavState) else None
    if isinstance(params, dict):
        return params.get("module")
    return None


def _find(nav: list[NavItem], module: str | None, title: str | None) -> NavItem | None:
    """Depth-first search of the (nested) navigation tree for one item.

    Top-level entries are menu *groups* carrying their leaves under ``items``;
    only leaves bind a module. Matches by ``title`` or by bound module name.
    """
    for item in nav:
        if title is not None and item.title == title:
            return item
        if module is not None and _item_module(item) == module:
            return item
        if item.items:
            hit = _find(item.items, module, title)
            if hit is not None:
                return hit
    return None


def _remove(nav: list[NavItem], module: str | None, title: str | None) -> int:
    """Remove every entry matching ``module``/``title`` from the (nested) tree in place.

    Returns the number of entries removed (descends into groups). Matches by ``title`` or
    by bound module name — the same criteria as :func:`_find`.
    """
    removed = 0
    kept: list[NavItem] = []
    for item in nav:
        matches = (title is not None and item.title == title) or (module is not None and _item_module(item) == module)
        if matches:
            removed += 1
            continue
        if item.items:
            removed += _remove(item.items, module, title)
        kept.append(item)
    nav[:] = kept
    return removed


class AppConfigAPI(BaseAPI):
    """Manage application navigation structure and module visibility gating.

    The navigation bar and module-visibility configuration is stored as a single
    document at ``GET|PUT /api/views/1/app``. Each navigation item's ``require``
    field controls visibility: an empty array means unrestricted, while an object
    with ``module`` and ``action`` keys restricts visibility to users with that
    permission on that module.

    Reads and writes both speak :class:`~pyfsr.models.NavItem`; use these methods
    to inspect or modify navigation items and their visibility constraints.
    """

    def get(self) -> dict[str, Any]:
        """Fetch the full application configuration document (the raw envelope).

        Returns:
            dict[str, Any]: The full application configuration document
            (``{"id": "app", "type": "app", "config": {"navigation": [...]}}``).
            For just the navigation tree, use :meth:`get_navigation`.
        """
        result = self.client.get(_APP_CONFIG)
        assert isinstance(result, dict)
        return result

    def get_navigation(self) -> list[NavItem]:
        """Return the navigation tree as typed :class:`~pyfsr.models.NavItem` models.

        Parses ``config.navigation`` recursively (including nested groups under
        ``items``). Items preserve unknown keys, so they round-trip cleanly back
        through :meth:`update_navigation`.

        Returns:
            List of :class:`~pyfsr.models.NavItem` (top-level entries; groups
            carry their children under ``.items``).

        Example:
            .. code-block:: python

                for item in client.app_config.get_navigation():
                    print(item.title, "group" if item.is_group else _item_module(item))
        """
        config = self.get()
        nav = config.get("config", {}).get("navigation", [])
        assert isinstance(nav, list)
        return [NavItem.model_validate(item) for item in nav]

    def find_navigation_item(self, module: str | None = None, title: str | None = None) -> NavItem | None:
        """Find a single navigation item by module name or title.

        Searches the current navigation (depth-first, including nested groups) for
        the first item matching the criteria. Returns ``None`` if not found.

        Args:
            module: Module name to match (e.g. ``"sla_templates"``), against the
                item's ``require.module`` or ``state.parameters.module``.
            title: Human-readable title to match (e.g. ``"Alerts"``), against the
                item's ``title`` (case-sensitive).

        Returns:
            The matching :class:`~pyfsr.models.NavItem`, or ``None``.

        Raises:
            ValueError: if both ``module`` and ``title`` are ``None``.

        Example:
            .. code-block:: python

                item = client.app_config.find_navigation_item(module="sla_templates")
                if item:
                    print(item.title, item.require)
        """
        if module is None and title is None:
            raise ValueError("find_navigation_item() requires module or title")
        return _find(self.get_navigation(), module, title)

    def update_navigation(self, items: list[NavItem]) -> dict[str, Any]:
        """Replace the entire ``navigation`` array and commit via PUT.

        Full replacement, not a merge: the whole ``navigation`` array is
        substituted with ``items`` (serialized to the wire shape) and the document
        is PUT back. To change one item surgically, read with :meth:`get_navigation`,
        mutate the item you want, and pass the whole list back here.

        Args:
            items: New list of :class:`~pyfsr.models.NavItem`.

        Returns:
            The API response (the updated configuration document).

        Raises:
            ValueError: if ``items`` is empty.

        Example:
            .. code-block:: python

                nav = client.app_config.get_navigation()
                sla = next((x for x in nav if x.title == "SLA Templates"), None)
                if sla:
                    sla.require = []  # make unrestricted
                client.app_config.update_navigation(nav)
        """
        if not items:
            raise ValueError("items list cannot be empty")

        config = self.get()
        config["config"]["navigation"] = [it.to_dict(by_alias=True, exclude_none=True) for it in items]
        result = self.client.put(_APP_CONFIG, data=config)
        assert isinstance(result, dict)
        return result

    def add_navigation_item(
        self,
        item: NavItem,
        *,
        parent: str | None = None,
        position: Literal["top", "bottom"] = "bottom",
    ) -> dict[str, Any]:
        """Add a single item to the navigation, then commit via PUT.

        Reads the current navigation, inserts ``item`` at the requested location,
        and writes the whole tree back. Existing items round-trip unchanged.

        Args:
            item: The :class:`~pyfsr.models.NavItem` to add. A leaf binds a module
                via ``state``/``require``; a group carries children under ``items``.
            parent: Where to add it. ``None`` (default) targets the top-level bar.
                Otherwise the string matches an existing entry by its ``title`` or
                bound module name (depth-first), and the item is inserted into that
                entry's ``items`` — making it a group if it was a leaf. Groups are
                matched by title; modules resolve to the leaf that binds them.
            position: ``"bottom"`` (default) appends; ``"top"`` prepends — within
                the target array (top-level or the parent group's children).

        Returns:
            The API response (the updated configuration document).

        Raises:
            ValueError: if ``position`` is invalid, or ``parent`` is given but no
                matching group is found.

        Example:
            .. code-block:: python

                from pyfsr.models import NavItem, NavRequire, NavState

                leaf = NavItem(
                    title="My Module",
                    icon="icon icon-bookmark",
                    state=NavState(name="main.modules.list", parameters={"module": "my_module"}),
                    require=NavRequire(module="my_module", action="read"),
                )
                client.app_config.add_navigation_item(leaf)                       # top-level, bottom
                client.app_config.add_navigation_item(leaf, parent="Incident Response", position="top")
        """
        if position not in ("top", "bottom"):
            raise ValueError("position must be 'top' or 'bottom'")

        nav = self.get_navigation()
        if parent is None:
            target = nav
        else:
            group = _find(nav, module=parent, title=parent)
            if group is None:
                raise ValueError(f"No navigation group found for parent {parent!r}")
            if group.items is None:
                group.items = []
            target = group.items

        if position == "top":
            target.insert(0, item)
        else:
            target.append(item)

        return self.update_navigation(nav)

    def ensure_navigation_item(
        self,
        item: NavItem,
        *,
        parent: str | None = None,
        position: Literal["top", "bottom"] = "bottom",
    ) -> dict[str, Any] | None:
        """Add ``item`` to the navigation only if it isn't already present.

        Idempotent wrapper around :meth:`add_navigation_item`: if an item
        matching the same ``module`` (from ``item.require.module`` or
        ``item.state.parameters.module``) or ``title`` already exists in the
        tree, this is a no-op returning ``None``; otherwise the item is added
        and the updated config document is returned. Re-running a deploy
        script won't duplicate the nav entry.

        Args:
            item: The :class:`~pyfsr.models.NavItem` to ensure.
            parent: Where to add it (same semantics as :meth:`add_navigation_item`).
            position: ``"bottom"`` (default) or ``"top"``.

        Returns:
            The updated config document when the item was added, or ``None``
            when it was already present.
        """
        module = _item_module(item)
        existing = self.find_navigation_item(module=module, title=item.title)
        if existing is not None:
            return None
        return self.add_navigation_item(item, parent=parent, position=position)

    def remove_navigation_item(
        self, module: str | None = None, title: str | None = None, *, missing_ok: bool = True
    ) -> dict[str, Any] | None:
        """Remove navigation entries matching ``module`` or ``title``, then commit via PUT.

        The inverse of :meth:`add_navigation_item`. Removes **every** matching entry from the
        (nested) tree — descending into groups — and writes the result back. Removing a group
        removes its children with it.

        Args:
            module: Module name to match (against ``require.module`` / ``state.parameters.module``).
            title: Title to match (case-sensitive).
            missing_ok: If True (default), a no-match is a no-op that returns ``None`` without
                writing. If False, a no-match raises ``ValueError``.

        Returns:
            The updated configuration document, or ``None`` if nothing matched and
            ``missing_ok`` is True.

        Raises:
            ValueError: if both ``module`` and ``title`` are ``None``, or if nothing matched
                and ``missing_ok`` is False.

        Example:
            .. code-block:: python

                client.app_config.remove_navigation_item(module="navprobe")
                client.app_config.remove_navigation_item(title="Old Section")
        """
        if module is None and title is None:
            raise ValueError("remove_navigation_item() requires module or title")

        nav = self.get_navigation()
        removed = _remove(nav, module, title)
        if removed == 0:
            if missing_ok:
                return None
            raise ValueError(f"no navigation item matched module={module!r}, title={title!r}")
        return self.update_navigation(nav)

    def set_navigation_visibility(
        self, module: str, *, require: NavRequire | dict[str, str] | list[Any] | None = None
    ) -> dict[str, Any]:
        """Update the visibility gate (``require``) for a single navigation item.

        Finds the item for ``module``, updates its ``require``, and commits.

        Args:
            module: Module name (e.g. ``"sla_templates"``).
            require: Visibility gate — ``None`` or ``[]`` for unrestricted, or a
                :class:`~pyfsr.models.NavRequire` / ``{"module": ..., "action": ...}``
                to gate by permission.

        Returns:
            The API response (the updated configuration document).

        Raises:
            ValueError: if no navigation item is found for the module.

        Example:
            .. code-block:: python

                # Make SLA Templates unrestricted
                client.app_config.set_navigation_visibility("sla_templates", require=[])

                # Gate by canRead permission
                client.app_config.set_navigation_visibility(
                    "sla_templates", require=NavRequire(module="sla_templates", action="canRead")
                )
        """
        nav = self.get_navigation()
        item = _find(nav, module=module, title=None)
        if item is None:
            raise ValueError(f"No navigation item found for module {module!r}")

        if require is None:
            item.require = []
        elif isinstance(require, dict):
            item.require = NavRequire.model_validate(require)
        else:
            item.require = require

        return self.update_navigation(nav)
