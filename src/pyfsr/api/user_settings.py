"""User Settings — per-user preferences under ``/api/3/user_settings``.

FortiSOAR stores each user's personal preferences (default landing module,
grid column choices, theme, saved filters, …) as a JSON blob keyed by setting
name. Unlike the appliance-wide :class:`~pyfsr.api.system_settings.SystemSettingsAPI`,
these are scoped to the *calling* user and are read and written through two
asymmetric, quirky paths that this wrapper hides:

* **Read** — there is no ``GET`` on ``user_settings``. The current user's
  settings come back embedded as the ``@settings`` object on
  ``GET /api/3/actors/current``. A dotted/slashed ``key`` indexes into it.
* **Write** — ``PUT /api/3/user_settings/current/<key>`` with the JSON value as
  the body. The value is deep-merged into the blob at ``key``.

Footguns this wrapper exists to encode (each cost a debugging session):

* ``/current/`` is the **only** working write path. ``PUT``/``PATCH`` against
  ``/api/3/user_settings/<uuid>`` returns 500/405.
* ``GET /api/3/settings`` is **404** — it is not the endpoint.
* ``PUT /api/3/actors/current`` with an ``@settings`` body returns 200 but does
  **not** persist — a silent no-op. Always write via ``set()``.

Accessed as ``client.user_settings``.

Example:
    >>> # Read one setting (or everything)
    >>> client.user_settings.get("grid/alerts")
    >>> client.user_settings.all()
    >>> # Write a setting (deep-merged at the key)
    >>> client.user_settings.set("grid/alerts", {"columns": ["name", "severity"]})
"""

from __future__ import annotations

from typing import Any

from .base import BaseAPI


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
