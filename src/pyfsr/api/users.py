"""The users module — ``client.users``.

Manage FortiSOAR users (People records + auth credentials) via ``/api/3/people``.
Each user is two linked records — a **People** profile and an internal **auth
user** — which ``/api/3/people`` creates atomically when the ``user`` and
``roles`` keys are supplied. Roles and teams may be given as UUIDs or friendly
names; names are resolved via a per-instance cache populated on first use.
"""

from __future__ import annotations

from typing import Any

from ..models import Role, Team, User
from ..pagination import extract_members
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

        client = FortiSOAR("https://your-fsr", username="csadmin", password="password", verify_ssl=False)

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

    def role_map(self) -> dict[str, str]:
        """Return ``{name: uuid}`` for all roles (delegates to :class:`~pyfsr.api.roles.RolesAPI`)."""
        return self.client.roles.role_map()

    def team_map(self) -> dict[str, str]:
        """Return ``{name: uuid}`` for all teams (delegates to :class:`~pyfsr.api.teams.TeamsAPI`)."""
        return self.client.teams.team_map()

    def _resolve_roles(self, roles: list[str]) -> list[str]:
        """Accept role UUIDs or names; return UUIDs (delegates to RolesAPI)."""
        return self.client.roles._resolve_roles(roles)

    def _resolve_teams(self, teams: list[str]) -> list[str]:
        """Accept team UUIDs or names; return UUIDs (delegates to TeamsAPI)."""
        return self.client.teams._resolve_teams(teams)

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
        typed: bool = True,
    ) -> User | dict[str, Any]:
        """
        Create a FortiSOAR user (People record + auth credentials).

        Args:
            loginid: Login username (must be unique).
            password: Initial password (must meet the appliance password policy).
            firstname: First name.
            lastname: Last name.
            email: Email address.
            roles: Role UUIDs **or** friendly names (e.g. ``["SOC Analyst"]``).
                At least one required.
            access_type: ``"Named"`` (default) or ``"Concurrent"``.
            active: Whether the account is active on creation. Defaults to ``True``.
            department: Optional department name.
            phone_work: Optional work phone number.
            phone_mobile: Optional mobile phone number.
            teams: Optional team UUIDs **or** friendly names (e.g. ``["Tier 1 SOC"]``).
            role_uuids: Deprecated alias for ``roles``.
            team_uuids: Deprecated alias for ``teams``.
            typed: parse the result into a :class:`~pyfsr.models.User` (default);
                pass ``False`` for the raw dict.

        Returns:
            The created People record.

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

        resp = self.client.post("/api/3/people", data=payload)
        return User.model_validate(resp) if typed else resp

    def find_by_email(self, email: str, *, typed: bool = True) -> User | None:
        """Find a user by email address (``GET /api/3/people?email=``).

        Returns ``None`` if no user has that email. ``email`` is the filterable
        unique key for People records (unlike ``loginid``, which lives on the
        nested auth-user and is not queryable on ``/api/3/people``).

        Args:
            email: the email address to look up.
            typed: parse the result into a :class:`~pyfsr.models.User` (default);
                pass ``False`` for the raw dict.

        Returns:
            The matching :class:`~pyfsr.models.User`, or ``None``.
        """
        members = extract_members(self.client.get("/api/3/people", params={"email": email}))
        if not members:
            return None
        return User.model_validate(members[0]) if typed else members[0]

    def get_or_create(
        self,
        loginid: str,
        password: str,
        firstname: str,
        lastname: str,
        email: str,
        roles: list[str],
        **kwargs: Any,
    ) -> tuple[User, bool]:
        """Idempotently ensure a user with ``email`` exists; return ``(user, created)``.

        Looks up by ``email`` (the filterable unique key on ``/api/3/people`` —
        ``loginid`` is not queryable). If found, the existing user is returned
        unchanged (``created=False``); otherwise a new user is created with the
        given credentials, roles, and profile fields (``created=True``).

        Accepts the same keyword arguments as :meth:`create` (``access_type``,
        ``active``, ``department``, ``teams``, etc.).

        Args:
            loginid: Login username (must be unique). Used only on the create path.
            password: Initial password. Used only on the create path.
            firstname: First name. Used only on the create path.
            lastname: Last name. Used only on the create path.
            email: Email address — the lookup key and the create-time email.
            roles: Role UUIDs or friendly names. Used only on the create path.
            **kwargs: Additional :meth:`create` arguments (``access_type``,
                ``active``, ``department``, ``teams``, etc.).

        Returns:
            ``(User, created)`` — the existing user with ``created=False``, or
            the newly-created user with ``created=True``.
        """
        existing = self.find_by_email(email)
        if existing is not None:
            return existing, False
        return self.create(
            loginid=loginid,
            password=password,
            firstname=firstname,
            lastname=lastname,
            email=email,
            roles=roles,
            **kwargs,
        ), True

    def list(self, params: dict | None = None, *, typed: bool = True) -> list[User] | dict[str, Any]:
        """
        List People records.

        Args:
            params: Optional query parameters (e.g. ``{"csActive": True}``).
            typed: parse rows into :class:`~pyfsr.models.User` (default); pass
                ``False`` for the raw Hydra collection dict.

        Returns:
            Typed :class:`~pyfsr.models.User` records, or the raw Hydra
            collection dict (``hydra:member`` list of People records) when
            ``typed=False``.
        """
        resp = self.client.get("/api/3/people", params=params)
        if typed:
            return [User.model_validate(m) for m in extract_members(resp)]
        return resp

    def get(self, person_uuid: str, *, typed: bool = True) -> User | dict[str, Any]:
        """
        Get a single People record by UUID.

        Args:
            person_uuid: The UUID of the person.
            typed: parse the result into a :class:`~pyfsr.models.User` (default);
                pass ``False`` for the raw dict.

        Returns:
            The People record.
        """
        resp = self.client.get(f"/api/3/people/{person_uuid}")
        return User.model_validate(resp) if typed else resp

    def update(self, person_uuid: str, *, typed: bool = True, **data: Any) -> User | dict[str, Any]:
        """
        Update a People record.

        Args:
            person_uuid: UUID of the person to update.
            typed: parse the result into a :class:`~pyfsr.models.User` (default);
                pass ``False`` for the raw dict.
            **data: Fields to update (e.g. ``department="SOC"``, ``csActive=False``).

        Returns:
            The updated People record.
        """
        resp = self.client.put(f"/api/3/people/{person_uuid}", data=data)
        return User.model_validate(resp) if typed else resp

    def deactivate(self, person_uuid: str, *, typed: bool = True) -> User | dict[str, Any]:
        """
        Deactivate a user account (sets ``csActive=False``).

        Args:
            person_uuid: UUID of the person to deactivate.
            typed: parse the result into a :class:`~pyfsr.models.User` (default);
                pass ``False`` for the raw dict.

        Returns:
            The updated People record.
        """
        return self.update(person_uuid, csActive=False, typed=typed)

    def list_roles(self, params: dict | None = None) -> list[Role]:
        """List all roles available for assignment (delegates to :class:`~pyfsr.api.roles.RolesAPI`).

        Returns typed :class:`~pyfsr.models.Role` records (dict-compatible:
        ``r["name"]`` / ``r["uuid"]`` still work).
        """
        return self.client.roles.list(params=params)

    def list_teams(self, params: dict | None = None) -> list[Team]:
        """List all teams available for assignment (delegates to :class:`~pyfsr.api.teams.TeamsAPI`).

        Returns typed :class:`~pyfsr.models.Team` records (dict-compatible:
        ``t["name"]`` / ``t["uuid"]`` still work).
        """
        return self.client.teams.list(params)

    def role_uuid_by_name(self, name: str) -> str | None:
        """Look up a role UUID by display name (case-sensitive); ``None`` if not found."""
        return self.client.roles.role_uuid_by_name(name)

    def team_uuid_by_name(self, name: str) -> str | None:
        """Look up a team UUID by display name (case-sensitive); ``None`` if not found."""
        return self.client.teams.team_uuid_by_name(name)
