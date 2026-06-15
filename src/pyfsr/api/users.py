from __future__ import annotations

from typing import Any

from .base import BaseAPI


class UsersAPI(BaseAPI):
    """
    Manage FortiSOAR users (People records + auth credentials) via ``/api/3/people``.

    Each FortiSOAR user has two linked records:
    - A **People** record (profile — name, email, department, …)
    - An **auth user** record (login credentials, managed internally by the auth service)

    The ``/api/3/people`` endpoint creates both atomically when the ``user`` and
    ``roles`` keys are supplied in the payload.

    Roles and teams can be specified as UUIDs **or** friendly names — the API resolves
    names automatically using a per-instance cache populated on first use.

    Example::

        from pyfsr import FortiSOAR

        client = FortiSOAR("https://your-fsr", ("csadmin", "password"), verify_ssl=False)

        # Create a user using friendly role/team names
        person = client.users.create(
            loginid="j.smith",
            password="Str0ng!Pass",
            firstname="Jane",
            lastname="Smith",
            email="j.smith@corp.example",
            roles=["SOC Analyst"],
            teams=["Tier 1 SOC"],
        )
    """

    _role_map: dict[str, str] | None = None
    _team_map: dict[str, str] | None = None

    def role_map(self) -> dict[str, str]:
        """Return ``{name: uuid}`` for all roles, cached for the lifetime of this instance."""
        if self._role_map is None:
            self._role_map = {
                r["name"]: r["uuid"] for r in self.list_roles() if r.get("name") and r.get("uuid")
            }
        return self._role_map

    def team_map(self) -> dict[str, str]:
        """Return ``{name: uuid}`` for all teams, cached for the lifetime of this instance."""
        if self._team_map is None:
            self._team_map = {
                t["name"]: t["uuid"] for t in self.list_teams() if t.get("name") and t.get("uuid")
            }
        return self._team_map

    def _resolve_roles(self, roles: list[str]) -> list[str]:
        """Accept role UUIDs or names; return UUIDs. Raises ValueError for unknown names."""
        rmap = None
        resolved = []
        for r in roles:
            if "-" in r and len(r) == 36:
                resolved.append(r)
            else:
                if rmap is None:
                    rmap = self.role_map()
                uuid = rmap.get(r)
                if uuid is None:
                    raise ValueError(f"Role not found: {r!r}. Available: {list(rmap)}")
                resolved.append(uuid)
        return resolved

    def _resolve_teams(self, teams: list[str]) -> list[str]:
        """Accept team UUIDs or names; return UUIDs. Raises ValueError for unknown names."""
        tmap = None
        resolved = []
        for t in teams:
            if "-" in t and len(t) == 36:
                resolved.append(t)
            else:
                if tmap is None:
                    tmap = self.team_map()
                uuid = tmap.get(t)
                if uuid is None:
                    raise ValueError(f"Team not found: {t!r}. Available: {list(tmap)}")
                resolved.append(uuid)
        return resolved

    def create(
        self,
        loginid: str,
        password: str,
        firstname: str,
        lastname: str,
        email: str,
        roles: list[str],
        *,
        access_type: str = "Named",
        active: bool = True,
        department: str | None = None,
        phone_work: str | None = None,
        phone_mobile: str | None = None,
        teams: list[str] | None = None,
        # legacy parameter names kept for backwards compatibility
        role_uuids: list[str] | None = None,
        team_uuids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a FortiSOAR user (People record + auth credentials).

        Args:
            loginid: Login username (must be unique).
            password: Initial password (must meet the appliance password policy).
            firstname: First name.
            lastname: Last name.
            email: Email address.
            roles: Role UUIDs **or** friendly names (e.g. ``["SOC Analyst"]``). At least one required.
            access_type: ``"Named"`` (default) or ``"Concurrent"``.
            active: Whether the account is active on creation. Defaults to ``True``.
            department: Optional department name.
            phone_work: Optional work phone number.
            phone_mobile: Optional mobile phone number.
            teams: Optional team UUIDs **or** friendly names (e.g. ``["Tier 1 SOC"]``).
            role_uuids: Deprecated alias for ``roles``.
            team_uuids: Deprecated alias for ``teams``.

        Returns:
            The created People record (dict).

        Raises:
            ValueError: If a role or team name cannot be resolved.
            pyfsr.exceptions.APIError: If the payload is invalid or a user with the
                same ``loginid`` already exists.
        """
        effective_roles = role_uuids if role_uuids is not None else roles
        effective_teams = team_uuids if team_uuids is not None else teams

        payload: dict[str, Any] = {
            "firstname": firstname,
            "lastname": lastname,
            "email": email,
            "csActive": active,
            "accessType": access_type,
            "roles": self._resolve_roles(effective_roles),
            "user": {
                "loginid": loginid,
                "password": password,
                "email": email,
            },
        }
        if department is not None:
            payload["department"] = department
        if phone_work is not None:
            payload["phoneWork"] = phone_work
        if phone_mobile is not None:
            payload["phoneMobile"] = phone_mobile
        if effective_teams:
            payload["teams"] = self._resolve_teams(effective_teams)

        return self.client.post("/api/3/people", data=payload)

    def list(self, params: dict | None = None) -> dict[str, Any]:
        """
        List People records.

        Args:
            params: Optional query parameters (e.g. ``{"csActive": True}``).

        Returns:
            Hydra collection dict with ``hydra:member`` list of People records.
        """
        return self.client.get("/api/3/people", params=params)

    def get(self, person_uuid: str) -> dict[str, Any]:
        """
        Get a single People record by UUID.

        Args:
            person_uuid: The UUID of the person.

        Returns:
            The People record dict.
        """
        return self.client.get(f"/api/3/people/{person_uuid}")

    def update(self, person_uuid: str, **data: Any) -> dict[str, Any]:
        """
        Update a People record.

        Args:
            person_uuid: UUID of the person to update.
            **data: Fields to update (e.g. ``department="SOC"``, ``csActive=False``).

        Returns:
            The updated People record dict.
        """
        return self.client.put(f"/api/3/people/{person_uuid}", data=data)

    def deactivate(self, person_uuid: str) -> dict[str, Any]:
        """
        Deactivate a user account (sets ``csActive=False``).

        Args:
            person_uuid: UUID of the person to deactivate.

        Returns:
            The updated People record dict.
        """
        return self.update(person_uuid, csActive=False)

    def list_roles(self, params: dict | None = None) -> list[dict[str, Any]]:
        """
        List all roles available for assignment.

        Returns:
            List of role dicts, each containing ``uuid`` and ``name``.
        """
        r = self.client.get("/api/3/roles", params=params)
        return r.get("hydra:member", [])

    def list_teams(self, params: dict | None = None) -> list[dict[str, Any]]:
        """
        List all teams available for assignment.

        Returns:
            List of team dicts, each containing ``uuid`` and ``name``.
        """
        r = self.client.get("/api/3/teams", params=params)
        return r.get("hydra:member", [])

    def role_uuid_by_name(self, name: str) -> str | None:
        """
        Look up a role UUID by its display name.

        Args:
            name: Exact role name (case-sensitive).

        Returns:
            UUID string, or ``None`` if not found.
        """
        return self.role_map().get(name)

    def team_uuid_by_name(self, name: str) -> str | None:
        """
        Look up a team UUID by its display name.

        Args:
            name: Exact team name (case-sensitive).

        Returns:
            UUID string, or ``None`` if not found.
        """
        return self.team_map().get(name)
