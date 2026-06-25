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

from pydantic import BaseModel

from .base import BaseAPI

_ENDPOINT = "/api/auth/config"


class AuthConfigRow(BaseModel):
    """One row of the DAS auth config (``/api/auth/config?section=`` ``hydra:member``).

    The value is heterogeneous across sections (strings, ints, bools), so
    ``value`` stays ``Any``; ``dataType`` carries the server's type hint.
    """

    model_config = {"extra": "allow"}

    id: int | None = None
    section: str | None = None
    key: str | None = None
    dataType: str | None = None
    value: Any = None


class AuthConfigAPI(BaseAPI):
    """Read/write the DAS authentication config (``/api/auth/config``)."""

    def get_raw(self, section: str) -> list[AuthConfigRow]:
        """Return the raw config rows for ``section`` (``TOKEN``, ``PASSWORD``, …).

        Each row is parsed into an :class:`AuthConfigRow`
        (``{id, section, key, dataType, value}``).
        """
        resp = self.client.get(_ENDPOINT, params={"section": section})
        return [AuthConfigRow.model_validate(r) for r in (resp or {}).get("hydra:member") or []]

    def get(self, section: str) -> dict[str, Any]:
        """Return a ``{key: value}`` dict for one config ``section``."""
        return {row.key: row.value for row in self.get_raw(section) if row.key is not None}

    def set(self, option: str, value: Any) -> dict[str, Any]:
        """Set a single config ``option`` to ``value``.

        ``PUT /api/auth/config`` with ``{"option": ..., "value": ...}``. The
        server validates bounds and returns ``{"response": "Success"}`` or an
        HTTP 400 with an ``{"Error": "..."}`` message.
        """
        return self.client.put(_ENDPOINT, data={"option": option, "value": value})

    # ------------------------------------------------------------- convenience
    def set_api_key_retrievable(self, enabled: bool) -> dict[str, Any]:
        """Toggle whether newly created API keys stay plaintext-recoverable.

        ``PUT /api/auth/config`` with the ``API-KEYS.retrievable_mode`` option.
        When **on at the time a key is created**, that key stays retrievable for
        its lifetime even if the global flag is later flipped off — so
        :meth:`~pyfsr.api.api_users.ApiKeyUsersAPI.get` with
        ``show_api_key=True`` can recover the plaintext on re-runs. The key
        plaintext is also returned in the create response regardless of this
        setting.
        """
        return self.set("retrievable_mode", bool(enabled))

    def is_api_key_retrievable(self) -> bool:
        """Whether the ``API-KEYS.retrievable_mode`` global flag is on."""
        return bool(self.get("API-KEYS").get("retrievable_mode", False))

    def set_idle_timeout(self, minutes: int) -> dict[str, Any]:
        """Idle auto-logout, in minutes (``TOKEN.idle_time``; server cap 360)."""
        return self.set("idle_time", minutes)

    def set_max_session(self, minutes: int) -> dict[str, Any]:
        """Absolute session length cap, in minutes (``TOKEN.max_session``)."""
        return self.set("max_session", minutes)

    def set_token_lifetime(self, seconds: int) -> dict[str, Any]:
        """JWT lifetime before refresh, in seconds (``TOKEN.token_lifetime``; cap 7200)."""
        return self.set("token_lifetime", seconds)
