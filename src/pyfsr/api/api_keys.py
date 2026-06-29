"""API-key bindings — ``/api/3/api_keys``.

An *API key* is the binding that attaches roles/teams to an **API-key user**
— the user record created via :class:`~pyfsr.api.api_users.ApiKeyUsersAPI`,
which carries the key material. Creating a usable key is two steps,
mirroring the product:

1. ``client.api_users.create(api_key_validity=...)`` → ``POST /api/auth/users``
   returns ``{"uuid", "api_key": {"key": "<plaintext>", "retrievable": bool}}``.
   The plaintext ``key`` is available **only here, at creation time** (unless
   the user was created while global ``retrievable_mode`` was on, in which
   case :meth:`~pyfsr.api.api_users.ApiKeyUsersAPI.get` with
   ``show_api_key=True`` recovers it).
2. ``client.api_keys.create(name=..., user_uuid=..., roles=..., teams=...)``
   → ``POST /api/3/api_keys`` binds roles/teams to that user.

``roles`` / ``teams`` accept IRIs (``/api/3/roles/<uuid>``,
``/api/3/teams/<uuid>``) **or** friendly names — resolved via the shared
:class:`~pyfsr.api.users.UsersAPI` maps, the same convention as
:meth:`~pyfsr.api.users.UsersAPI.create`.

Accessed as ``client.api_keys``.

Example:
    >>> u = client.api_users.create(api_key_validity=365)
    >>> plaintext = u["api_key"]["key"]
    >>> client.api_keys.create(name="repro-teamb", user_uuid=u["uuid"], teams=["TeamB"])
"""

from __future__ import annotations

from typing import Any

from ..models import ApiKey
from ..pagination import extract_members
from .base import BaseAPI

_BASE = "/api/3/api_keys"


def _api_key_plaintext(client: Any, user_uuid: str) -> str | None:
    """Recover an API-key user's plaintext (``GET /api/auth/users?show_api_key``).

    Returns ``None`` when the key is masked — i.e. its per-key ``retrievable``
    flag is false (the key was created while global ``retrievable_mode`` was off).
    A masked key is non-empty (``"xxxx…d517"``) but useless, so the ``retrievable``
    flag — not the key's truthiness — is what decides recoverability. Callers
    should :meth:`~pyfsr.api.api_users.ApiKeyUsersAPI.regenerate` when this
    returns ``None`` (verified live: toggling ``retrievable_mode`` on does not
    retroactively unmask existing keys).
    """
    u = client.api_users.get(user_uuid, show_api_key=True)
    ak = u.get("api_key") or {}
    if not ak.get("retrievable"):
        return None
    return ak.get("key")


def _response_plaintext(user: Any) -> str | None:
    """Pull the plaintext key out of a create/regenerate ``ApiKeyUser`` response.

    ``POST``/``PUT /api/auth/users`` echo the freshly minted key under
    ``api_key.key`` — the *only* moment the plaintext is exposed (a masked key
    can never be unmasked afterwards, regardless of ``retrievable_mode``). This
    avoids the ``retrievable_mode`` global flag entirely, which on FortiSOAR
    7.6.5 / 8.0.0 activates a broken ``encrypt(preserve_compatibility=...)``
    path that 400s all API-key creation.
    """
    ak = user.get("api_key") or {} if hasattr(user, "get") else {}
    return ak.get("key") or None


class ApiKeysAPI(BaseAPI):
    """Create and inspect API-key bindings (roles/teams on an API-key user)."""

    def list(self, params: dict[str, Any] | None = None) -> list[ApiKey]:
        """List API-key bindings (``GET /api/3/api_keys``) as typed :class:`~pyfsr.models.ApiKey` records.

        Each member carries ``name``, ``userId``, ``roles``, ``teams`` (the key
        value itself is masked).
        """
        return [ApiKey.model_validate(m) for m in extract_members(self.client.get(_BASE, params=params))]

    def get(self, uuid: str) -> ApiKey:
        """Fetch one API-key binding by uuid (``GET /api/3/api_keys/{uuid}``)."""
        return ApiKey.model_validate(self.client.get(f"{_BASE}/{uuid}"))

    def create(
        self,
        *,
        name: str,
        user_uuid: str,
        roles: list[str] | None = None,
        teams: list[str] | None = None,
    ) -> ApiKey:
        """Bind roles/teams to an API-key user (``POST /api/3/api_keys``).

        Args:
            name: friendly identifier for the key binding.
            user_uuid: the API-key user's uuid (from
                :meth:`~pyfsr.api.api_users.ApiKeyUsersAPI.create`).
            roles: optional role IRIs or names (resolved via
                :meth:`~pyfsr.api.roles.RolesAPI.role_uuid_by_name`).
            teams: optional team IRIs or names (resolved via
                :meth:`~pyfsr.api.teams.TeamsAPI.team_uuid_by_name`).
        """
        body: dict[str, Any] = {"name": name, "userId": user_uuid}
        if roles is not None:
            body["roles"] = self._resolve_roles(list(roles))
        if teams is not None:
            body["teams"] = self._resolve_teams(list(teams))
        return ApiKey.model_validate(self.client.post(_BASE, data=body))

    def update(self, uuid: str, **fields: Any) -> ApiKey:
        """Partially update an API-key binding (``PUT /api/3/api_keys/{uuid}``).

        Pass only the keys to change, e.g. ``teams=[...]``, ``roles=[...]``.
        ``roles``/``teams`` accept IRIs or names, like :meth:`create`.
        """
        if "roles" in fields and fields["roles"] is not None:
            fields["roles"] = self._resolve_roles(list(fields["roles"]))
        if "teams" in fields and fields["teams"] is not None:
            fields["teams"] = self._resolve_teams(list(fields["teams"]))
        return ApiKey.model_validate(self.client.put(f"{_BASE}/{uuid}", data=fields))

    def delete(self, uuid: str) -> None:
        """Delete an API-key binding (``DELETE /api/3/api_keys/{uuid}``).

        Removes the role/team binding. The underlying API-key *user* is separate
        — revoke it via
        :meth:`~pyfsr.api.api_users.ApiKeyUsersAPI.revoke` if it should no longer
        authenticate.
        """
        self.client.delete(f"{_BASE}/{uuid}")

    def get_plaintext(self, user_uuid: str) -> str | None:
        """Recover an API-key user's plaintext key, or ``None`` if it is masked.

        Public wrapper over the ``GET /api/auth/users?show_api_key`` recovery flow.
        Returns ``None`` when the key's per-key ``retrievable`` flag is false (it was
        minted while global ``retrievable_mode`` was off) — a masked key is non-empty
        but useless, so ``None`` signals "regenerate it" rather than "no key". Toggling
        ``retrievable_mode`` on does **not** retroactively unmask existing keys, so a
        ``None`` here means :meth:`~pyfsr.api.api_users.ApiKeyUsersAPI.regenerate`.
        """
        return _api_key_plaintext(self.client, user_uuid)

    def get_or_create(
        self,
        *,
        name: str,
        user_uuid: str,
        roles: list[str] | None = None,
        teams: list[str] | None = None,
    ) -> tuple[ApiKey, bool]:
        """Find an existing binding by ``name``, or create if absent.

        Returns ``(binding, created)`` — idempotent by ``name`` (the natural
        key for an API-key binding). When an existing binding is reused, the
        plaintext key is **not** available here; capture it from
        :meth:`~pyfsr.api.api_users.ApiKeyUsersAPI.create` or recover via
        ``show_api_key=True`` (requires ``retrievable_mode``).

        NB: this matches on ``name`` only; a pre-existing binding bound to a
        *different* user or teams is returned as-is. Call :meth:`update` to
        reconcile teams/roles if a stale binding must be repaired.
        """
        for k in self.list():
            if k.get("name") == name:
                return k, False
        return self.create(name=name, user_uuid=user_uuid, roles=roles, teams=teams), True

    def ensure_usable(
        self,
        *,
        name: str,
        teams: list[str] | None = None,
        roles: list[str] | None = None,
        api_key_validity: int = 365,
    ) -> tuple[ApiKey, str]:
        """Find or create an API-key binding by ``name`` and return its plaintext.

        The one-call version of the two-step key lifecycle for callers that need
        to *use* a key (e.g. to build a second client that authenticates as a
        specific team). Coordinates the pieces :meth:`get_or_create` leaves to
        the caller:

        1. Finds an existing binding by ``name``; if none, creates the API-key
           user (:meth:`~pyfsr.api.api_users.ApiKeyUsersAPI.create`) and binds it
           (:meth:`create`). A freshly created user returns its plaintext key in
           the create response.
        2. On a *reused* binding the original plaintext is gone (a masked key can
           never be unmasked after the fact), so
           :meth:`~pyfsr.api.api_users.ApiKeyUsersAPI.regenerate` mints a fresh
           key — its plaintext comes back in the regenerate **response**. This
           invalidates the previous key value for that binding.
        3. Reconciles ``teams``/``roles`` on a reused binding.

        This deliberately does **not** touch the global ``retrievable_mode``
        flag. ``retrievable_mode`` only enables recovering a key *later* via
        ``GET …?show_api_key=true``; capturing the plaintext from the
        create/regenerate response makes it unnecessary. Toggling it on also
        activates a broken ``apikeys_helper`` branch that calls
        ``encrypt(preserve_compatibility=...)`` — unsupported by the shipped
        ``PasswordModule`` on FortiSOAR 7.6.5 / 8.0.0 — which makes *all*
        API-key creation fail with HTTP 400 on those builds.

        Idempotent by ``name`` (reuse regenerates). Returns ``(binding, plaintext)``.
        ``teams``/``roles`` accept IRIs or names (resolved like :meth:`create`).

        Raises:
            RuntimeError: if the create/regenerate response carries no plaintext.
        """
        client = self.client

        existing = next((k for k in self.list() if k.get("name") == name), None)
        if existing:
            binding = existing
            user_uuid = existing["userId"]
            created = False
            # The original plaintext is unrecoverable on reuse — mint a fresh one
            # and read it straight from the regenerate response.
            plaintext = _response_plaintext(client.api_users.regenerate(user_uuid, api_key_validity=api_key_validity))
        else:
            u = client.api_users.create(api_key_validity=api_key_validity)
            user_uuid = u["uuid"]
            binding = self.create(name=name, user_uuid=user_uuid, roles=roles, teams=teams)
            created = True
            plaintext = _response_plaintext(u)

        # Reconcile teams/roles on a reused binding (get_or_create matches on
        # name only and returns a possibly-stale binding as-is). Only pass the
        # keys that were supplied so we don't null out the other side.
        if not created:
            reconcile: dict[str, Any] = {}
            if teams is not None:
                reconcile["teams"] = teams
            if roles is not None:
                reconcile["roles"] = roles
            if reconcile:
                self.update(binding["uuid"], **reconcile)

        if not plaintext:
            raise RuntimeError(
                f"create/regenerate response for API key {name!r} (user {user_uuid}) carried no plaintext key value"
            )
        return binding, plaintext

    # --------------------------------------------------------------- helpers
    def _resolve_roles(self, roles: list[str]) -> list[str]:
        """Accept role UUIDs or names; return UUIDs (delegates to RolesAPI)."""
        return self.client.roles._resolve_roles(roles)

    def _resolve_teams(self, teams: list[str]) -> list[str]:
        """Accept team UUIDs or names; return UUIDs (delegates to TeamsAPI)."""
        return self.client.teams._resolve_teams(teams)
