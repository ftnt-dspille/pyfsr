"""Typed models for the application navigation document (``/api/views/1/app``).

The navigation bar is a single document whose ``config.navigation`` array is a
(nested) tree of :class:`NavItem`. Top-level entries are either leaves (binding
a module via :attr:`NavItem.state` / :attr:`NavItem.require`) or *groups* that
carry their children under :attr:`NavItem.items`.

These models subclass :class:`~pyfsr.models._integration.ApiResult`, so they
stay dict-compatible (``item["title"]`` keeps working) and preserve any unknown
keys (``extra="allow"``) for lossless round-tripping back through a PUT.

Field set captured from a live 7.6.5 appliance ``/api/views/1/app`` response
(see ``AppConfigAPI`` for the read/write methods).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ._integration import ApiResult


class NavRequire(ApiResult):
    """The visibility gate on a navigation leaf.

    ``{"module": "alerts", "action": "read"}`` means the leaf is shown only to
    users with the ``read`` permission on the ``alerts`` module. Groups carry no
    ``require`` (it comes back ``None`` / absent), and an empty array ``[]`` on
    a leaf means *unrestricted* — see :attr:`NavItem.require`.
    """

    module: str | None = None
    action: str | None = None


class NavState(ApiResult):
    """The Angular UI-router state a navigation leaf routes to.

    ``parameters`` is usually ``{"module": "<name>"}`` for module-list entries,
    but comes back as an empty list ``[]`` for parameterless states (e.g. the
    dashboard), so it is typed permissively.
    """

    name: str | None = None
    parameters: dict[str, Any] | list[Any] | None = None


class NavItem(ApiResult):
    """A single navigation entry — a leaf or a group.

    A *leaf* binds a module through :attr:`state` / :attr:`require`. A *group*
    carries child entries under :attr:`items` and has no ``state``/``require``.
    All fields are optional because the wire shape differs between the two and
    between appliance versions; unknown keys are preserved for round-tripping.
    """

    title: str | None = None
    icon: str | None = None
    state: NavState | None = None
    # An object gates by permission; an empty list ``[]`` means unrestricted;
    # ``None`` (the default) means no gate is present (groups, parameterless items).
    require: NavRequire | list[Any] | None = None
    items: list[NavItem] | None = None
    # Presentation / merge flags observed on the wire (camelCase aliases).
    edit_mode: bool | None = Field(default=None, alias="editMode")
    exists: bool | None = None
    include: bool | None = None
    is_enabled: bool | None = Field(default=None, alias="isEnabled")
    open: bool | None = None
    open_status: bool | None = Field(default=None, alias="openStatus")
    merge_type: str | None = Field(default=None, alias="mergeType")

    @property
    def is_group(self) -> bool:
        """True if this entry has children (i.e. is a menu group)."""
        return isinstance(self.items, list)


# Resolve the forward reference for the self-referential ``items`` field.
NavItem.model_rebuild()
