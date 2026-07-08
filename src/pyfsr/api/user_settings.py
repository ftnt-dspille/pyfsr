"""User Settings — per-user preferences under ``/api/3/user_settings``.

FortiSOAR stores each user's personal preferences (default landing module,
grid column choices, theme, saved filters, …) as a JSON blob keyed by setting
name. Unlike the appliance-wide :class:`~pyfsr.api.system_settings.SystemSettingsAPI`,
these are scoped to the *calling* user and are read, written, and cleared
through paths that this wrapper hides:

* **Read** — ``GET /api/3/user_settings/current/<key>`` returns the value at
  ``key`` directly (404 if the key doesn't exist). ``all()``/``get()`` instead
  read the whole blob off ``GET /api/3/actors/current`` (its ``@settings``
  object) in one round trip — cheaper when you want more than one key.
* **Write** — ``PUT /api/3/user_settings/current/<key>`` with the JSON value as
  the body. The value is deep-merged into the blob at ``key``.
* **Delete** — ``DELETE /api/3/user_settings/current/<key>`` removes ``key``
  (and everything under it) from the blob. 404 if it doesn't exist.

Footguns this wrapper exists to encode (each cost a debugging session):

* ``/current/`` is the **only** working write path. ``PUT``/``PATCH`` against
  ``/api/3/user_settings/<uuid>`` returns 500/405.
* ``GET /api/3/settings`` is **404** — it is not the endpoint.
* ``PUT /api/3/actors/current`` with an ``@settings`` body returns 200 but does
  **not** persist — a silent no-op. Always write via ``set()``.
* Deleting an intermediate node (e.g. the whole ``details/alerts`` node)
  removes *sibling* keys under it too (``subtabs``, ``openCollab``, …), not
  just the one you meant to clear — delete the most specific key you can.

Accessed as ``client.user_settings``.

Example:
    >>> client = demo_client()
    >>> # Read one setting (or everything) off the actors/current @settings blob
    >>> client.user_settings.get("grid/alerts")
    {'columns': ['name', 'severity']}
    >>> sorted(client.user_settings.all())
    ['grid', 'user']
    >>> # View-template convenience wrappers (module detail page)
    >>> client.user_settings.get_view_template("alerts")               # uuid
    'd77cd7b5-3e0b-43b5-8c9b-54651dacdebe'
    >>> client.user_settings.get_view_template_name("alerts")          # name
    'CrowdStrike'
    >>> sorted(client.user_settings.set_view_template("alerts", "CrowdStrike"))  # by name
    ['grid', 'user']
    >>> # get_direct() reads the key straight from /current/<key>, unwrapped
    >>> client.user_settings.get_direct("user/view/details/alerts/viewTemplate")
    'd77cd7b5-3e0b-43b5-8c9b-54651dacdebe'
    >>> client.user_settings.clear_view_template("alerts") is None
    True
"""

from __future__ import annotations

import re
from typing import Any

from .base import BaseAPI

#: A ``system_view_templates`` uuid, e.g. ``d77cd7b5-3e0b-43b5-8c9b-54651dacdebe``.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


class UserSettingsAPI(BaseAPI):
    """Read and write the calling user's personal settings blob."""

    _ENDPOINT = "/api/3/user_settings"
    _ACTOR = "/api/3/actors/current"

    def all(self) -> dict[str, Any]:
        """Return the full ``@settings`` blob for the current user.

        Reads ``GET /api/3/actors/current`` and returns its ``@settings`` object
        (an empty dict if the user has no settings yet).
        """
        actor = self.client.get(self._ACTOR)
        return (actor or {}).get("@settings") or {}

    def get(self, key: str | None = None, default: Any = None) -> Any:
        """Return a single user setting by ``key`` (or the whole blob if omitted).

        ``key`` is a ``/``-separated path into the settings object, e.g.
        ``"grid/alerts"`` reads ``@settings["grid"]["alerts"]``. Returns
        ``default`` if any segment is missing.
        """
        settings = self.all()
        if key is None:
            return settings
        node: Any = settings
        for segment in key.split("/"):
            if not isinstance(node, dict) or segment not in node:
                return default
            node = node[segment]
        return node

    def set(self, key: str, value: Any) -> dict[str, Any]:
        """Write ``value`` at ``key`` via the only working path (``/current/<key>``).

        ``PUT /api/3/user_settings/current/<key>`` with ``value`` as the JSON
        body; FortiSOAR deep-merges it into the user's settings at ``key``.
        ``key`` may be ``/``-separated to target a nested path. Returns the API
        response.

        Note: do **not** try to persist user settings by PUTing ``@settings`` on
        ``actors/current`` — that returns 200 but silently drops the write.
        """
        if not key:
            raise ValueError("set() requires a non-empty key")
        return self.client.put(f"{self._ENDPOINT}/current/{key}", data=value)

    def get_direct(self, key: str) -> Any:
        """Read ``key`` straight from ``GET /api/3/user_settings/current/<key>``.

        Unlike :meth:`get`, this does not go through the ``@settings`` blob on
        ``actors/current`` — it's one HTTP call per key, so prefer :meth:`get`
        when reading more than one key. Raises
        :class:`~pyfsr.exceptions.ResourceNotFoundError` (404) if ``key``
        doesn't exist rather than returning a default.
        """
        if not key:
            raise ValueError("get_direct() requires a non-empty key")
        return self.client.get(f"{self._ENDPOINT}/current/{key}")

    def delete(self, key: str) -> Any:
        """Remove ``key`` (and everything nested under it) via ``DELETE /current/<key>``.

        Raises :class:`~pyfsr.exceptions.ResourceNotFoundError` (404) if
        ``key`` doesn't exist. Deleting an intermediate node also removes its
        sibling keys — delete the most specific key you can.
        """
        if not key:
            raise ValueError("delete() requires a non-empty key")
        return self.client.delete(f"{self._ENDPOINT}/current/{key}")

    def get_view_template(self, module: str, default: Any = None) -> Any:
        """Return the saved detail-page view-template uuid for ``module``.

        Reads ``user/view/details/<module>/viewTemplate``, e.g. the value the
        UI writes when you pick a non-default view template on an alert's
        detail page. Returns ``default`` if none is set. See
        :meth:`get_view_template_name` for the human-readable name instead.
        """
        return self.get(f"user/view/details/{module}/viewTemplate", default=default)

    def get_view_template_name(self, module: str, default: Any = None) -> Any:
        """Return the saved detail-page view-template's **name** for ``module``.

        Looks up the uuid from :meth:`get_view_template`, then resolves it
        against :meth:`ViewTemplatesAPI.list_templates
        <pyfsr.api.view_templates.ViewTemplatesAPI.list_templates>` for
        ``module``. Returns ``default`` if no template is set, or if the
        stored uuid no longer matches any template (e.g. it was deleted).
        """
        uuid = self.get_view_template(module)
        if not uuid:
            return default
        for t in self.client.view_templates.list_templates(module=module):
            if t.get("uuid") == uuid:
                return t.get("name")
        return default

    def resolve_view_template(self, module: str, template: str, *, kind: str = "detail") -> str:
        """Resolve ``template`` (a name or a uuid) to its uuid for ``module``.

        ``template`` already shaped like a uuid is returned unchanged (no
        lookup, no matching against real templates). Otherwise it's matched
        by ``name`` (case-insensitive) against
        :meth:`ViewTemplatesAPI.list_templates
        <pyfsr.api.view_templates.ViewTemplatesAPI.list_templates>` for
        ``module``, restricted to layout ``kind`` (default ``"detail"``, since
        that's the only layout ``user/view/details/<module>/viewTemplate``
        ever writes). This matters because template **names are not unique
        across layouts** — e.g. every module ships a "Default Layout" row for
        each of ``list``/``detail``/``form``; matching by name alone would
        silently resolve to whichever layout happened to list first.

        Raises:
            ValueError: if ``template`` isn't a uuid and no template named
                ``template`` exists for ``(module, kind)``.
        """
        if _UUID_RE.match(template):
            return template
        want = template.strip().lower()
        for t in self.client.view_templates.list_templates(module=module):
            if t.get("viewOptions") == kind and str(t.get("name", "")).strip().lower() == want:
                return t["uuid"]
        raise ValueError(f"No {kind!r} view template named {template!r} for module {module!r}")

    def set_view_template(self, module: str, template: str) -> dict[str, Any]:
        """Set the detail-page view-template for ``module`` to ``template``.

        ``template`` may be a template **name** (e.g. ``"My Custom Layout"``,
        resolved via :meth:`resolve_view_template`) or a uuid — pass whichever
        is convenient; names are usually easier to work with than uuids.
        """
        uuid = self.resolve_view_template(module, template)
        return self.set(f"user/view/details/{module}/viewTemplate", uuid)

    def clear_view_template(self, module: str) -> Any:
        """Clear the saved detail-page view-template for ``module`` (reverts to default)."""
        return self.delete(f"user/view/details/{module}/viewTemplate")
