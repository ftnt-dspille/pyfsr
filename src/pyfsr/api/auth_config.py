"""Authentication / DAS config — ``/api/auth/config`` (session timeout, 2FA, …).

These settings live in the ``cyops-auth`` (DAS) service, not the PHP platform,
so they are reached under the ``/api/auth/*`` prefix rather than ``/api/3``.
The most-tuned values are in the ``TOKEN`` section — idle timeout, token
lifetime, max session length.

Accessed as ``client.auth_config``. Requires username/password auth (the DAS
``/api/auth/*`` routes reject API-key auth).

Server-enforced caps (PUT returns HTTP 400 otherwise):
    * ``idle_time``      ≤ 360  minutes (6 h)
    * ``token_lifetime`` ≤ 7200 seconds (2 h)

Example:
    >>> client.auth_config.get("TOKEN")["idle_time"]
    30
    >>> client.auth_config.set_idle_timeout(360)        # 6 h — the max
    >>> client.auth_config.set("max_session", 1440)     # 24 h absolute cap
"""

from __future__ import annotations

from typing import Any

from .base import BaseAPI

_ENDPOINT = "/api/auth/config"


class AuthConfigAPI(BaseAPI):
    """Read/write the DAS authentication config (``/api/auth/config``)."""

    def get_raw(self, section: str) -> list[dict[str, Any]]:
        """Return the raw config rows for ``section`` (``TOKEN``, ``PASSWORD``, …).

        Each row is ``{id, section, key, dataType, value}``.
        """
        resp = self.client.get(_ENDPOINT, params={"section": section})
        return (resp or {}).get("hydra:member") or []

    def get(self, section: str) -> dict[str, Any]:
        """Return a ``{key: value}`` dict for one config ``section``."""
        return {row["key"]: row["value"] for row in self.get_raw(section)}

    def set(self, option: str, value: Any) -> dict[str, Any]:
        """Set a single config ``option`` to ``value``.

        ``PUT /api/auth/config`` with ``{"option": ..., "value": ...}``. The
        server validates bounds and returns ``{"response": "Success"}`` or an
        HTTP 400 with an ``{"Error": "..."}`` message.
        """
        return self.client.put(_ENDPOINT, data={"option": option, "value": value})

    # ------------------------------------------------------------- convenience
    def set_idle_timeout(self, minutes: int) -> dict[str, Any]:
        """Idle auto-logout, in minutes (``TOKEN.idle_time``; server cap 360)."""
        return self.set("idle_time", minutes)

    def set_max_session(self, minutes: int) -> dict[str, Any]:
        """Absolute session length cap, in minutes (``TOKEN.max_session``)."""
        return self.set("max_session", minutes)

    def set_token_lifetime(self, seconds: int) -> dict[str, Any]:
        """JWT lifetime before refresh, in seconds (``TOKEN.token_lifetime``; cap 7200)."""
        return self.set("token_lifetime", seconds)
