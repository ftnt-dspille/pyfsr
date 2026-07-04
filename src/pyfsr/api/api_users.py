"""API-key user management (``/api/auth/users``).

The lifecycle of *API-key users* — the user records that carry key material,
distinct from the people/roles/teams of :class:`~pyfsr.api.users.UsersAPI`.
Accessed as ``client.api_users``.

Creating a usable key is two steps, mirroring the product: create the API-key
user here (:meth:`ApiKeyUsersAPI.create`), then attach roles/teams by POSTing its
``uuid`` to ``/api/3/api_keys``. The ``uuid`` used throughout is the ``userId``
returned by ``GET /api/3/api_keys``.

Example:
    >>> u = client.api_users.create(api_key_validity=365)   # type=9, status=1
    >>> client.api_users.get(u["uuid"], show_api_key=True)
    >>> client.api_users.revoke(u["uuid"])
"""

from __future__ import annotations

from typing import Any

from ..exceptions import APIError, ApikeyCreateUnavailable
from ..models import ApiKeyUser
from .base import BaseAPI

_BASE = "/api/auth/users"

#: ``type`` discriminator for an API-key user.
_TYPE_API_KEY = 9
#: ``status`` value meaning active.
_STATUS_ACTIVE = 1

#: Valid ``operation`` values for :meth:`lifecycle` (PUT discriminator).
_OPS = frozenset({"REVOKE", "ACTIVATE", "DEACTIVATE", "REGENERATE", "RESET_VALIDITY"})


class ApiKeyUsersAPI(BaseAPI):
    """Create, inspect, and run lifecycle ops on API-key users."""

    @staticmethod
    def _members(resp: Any) -> list[dict[str, Any]]:
        """Return the ``usersresp`` list from a GET/POST response, or ``[]``."""
        members = resp.get("usersresp") if isinstance(resp, dict) else None
        return members if isinstance(members, list) else []

    def get(self, uuid: str, *, show_api_key: bool = False) -> ApiKeyUser:
        """Look up an API-key user by uuid (``GET /api/auth/users?uuid=``).

        The response masks the key by default; ``show_api_key=True`` returns the
        plaintext — but only when the key was created with ``retrievable_mode``
        (the per-key ``api_key.retrievable`` flag is set at creation; toggling the
        global flag on later does **not** retroactively unmask existing keys).
        Unwraps the ``{"usersresp": [user]}`` envelope and parses the user into an
        :class:`~pyfsr.models.ApiKeyUser` (dict-compatible: ``u["uuid"]``,
        ``u["api_key"]["key"]`` still work).
        """
        params: dict[str, Any] = {"uuid": uuid}
        if show_api_key:
            params["show_api_key"] = "true"
        resp = self.client.get(_BASE, params=params)
        members = self._members(resp)
        return ApiKeyUser.model_validate(members[0] if members else (resp if isinstance(resp, dict) else {}))

    def query(self, uuids: list[str], *, show_api_key: bool = False) -> list[ApiKeyUser]:
        """Bulk-fetch API-key users by uuid (``POST /api/auth/query/users``).

        ``uuids`` are ``userId`` values from ``GET /api/3/api_keys``. Keys are
        masked unless ``show_api_key=True`` (and the user was ``retrievable_mode``).
        Returns typed :class:`~pyfsr.models.ApiKeyUser` records.
        """
        body: dict[str, Any] = {"users": list(uuids)}
        if show_api_key:
            body["show_api_key"] = True
        resp = self.client.post("/api/auth/query/users", data=body)
        return [ApiKeyUser.model_validate(m) for m in self._members(resp)]

    def create(
        self,
        *,
        api_key_validity: int,
        type: int = _TYPE_API_KEY,
        status: int = _STATUS_ACTIVE,
    ) -> ApiKeyUser:
        """Create an API-key user (``POST /api/auth/users``).

        Creates the user record carrying the key material; its returned ``uuid``
        feeds ``POST /api/3/api_keys`` to attach roles/teams. ``api_key_validity``
        is the key's validity in days. ``type=9`` (API-key user) and ``status=1``
        (active) are the defaults — all three fields are required by the endpoint.

        Raises:
            ApikeyCreateUnavailable: when the box has the 7.6.5/8.0.0
                ``encrypt(preserve_compatibility)`` product bug (global
                ``retrievable_mode`` on). The exception carries the workaround
                (``client.auth_config.set_api_key_retrievable(False)``) instead
                of surfacing the cryptic encrypt 400.
        """
        body = {"type": type, "status": status, "api_key_validity": api_key_validity}
        try:
            return ApiKeyUser.model_validate(self.client.post(_BASE, data=body))
        except APIError as exc:
            sig = f"{exc.message or ''} {exc.error_type or ''}"
            if "preserve_compatibility" in sig or "encrypt()" in sig:
                raise ApikeyCreateUnavailable(response=exc.response, original_message=exc.message) from exc
            raise

    def lifecycle(
        self,
        uuid: str,
        operation: str,
        *,
        key_type: str = "API_KEY",
        api_key_validity: int | None = None,
    ) -> ApiKeyUser:
        """Run a lifecycle operation on an API-key user (``PUT /api/auth/users``).

        One endpoint, discriminated by ``operation``:

        - ``REVOKE`` — permanent deactivation.
        - ``ACTIVATE`` / ``DEACTIVATE`` — toggle active state.
        - ``REGENERATE`` — mint a new key value.
        - ``RESET_VALIDITY`` — extend/reset validity (pass ``api_key_validity``).

        Prefer the named convenience wrappers (:meth:`revoke`, :meth:`activate`,
        …) over calling this directly.
        """
        op = operation.upper()
        if op not in _OPS:
            raise ValueError(f"operation must be one of {sorted(_OPS)}, got {operation!r}")
        body: dict[str, Any] = {"uuid": uuid, "key_type": key_type, "operation": op}
        if api_key_validity is not None:
            body["api_key_validity"] = api_key_validity
        return ApiKeyUser.model_validate(self.client.put(_BASE, data=body))

    def revoke(self, uuid: str, *, key_type: str = "API_KEY") -> ApiKeyUser:
        """Permanently revoke an API-key user (lifecycle ``REVOKE``)."""
        return self.lifecycle(uuid, "REVOKE", key_type=key_type)

    def activate(self, uuid: str, *, key_type: str = "API_KEY") -> ApiKeyUser:
        """Activate an API-key user (lifecycle ``ACTIVATE``)."""
        return self.lifecycle(uuid, "ACTIVATE", key_type=key_type)

    def deactivate(self, uuid: str, *, key_type: str = "API_KEY") -> ApiKeyUser:
        """Deactivate an API-key user (lifecycle ``DEACTIVATE``)."""
        return self.lifecycle(uuid, "DEACTIVATE", key_type=key_type)

    def regenerate(self, uuid: str, *, api_key_validity: int = 365, key_type: str = "API_KEY") -> ApiKeyUser:
        """Regenerate an API-key user's key (lifecycle ``REGENERATE``).

        ``api_key_validity`` (days) is **required by the server** for a
        regenerate — omitting it errors — so it carries a default here. The
        fresh plaintext is returned under ``api_key.key`` in the response.
        """
        return self.lifecycle(uuid, "REGENERATE", key_type=key_type, api_key_validity=api_key_validity)

    def reset_validity(self, uuid: str, api_key_validity: int, *, key_type: str = "API_KEY") -> ApiKeyUser:
        """Reset an API-key user's validity window (lifecycle ``RESET_VALIDITY``)."""
        return self.lifecycle(uuid, "RESET_VALIDITY", key_type=key_type, api_key_validity=api_key_validity)
