"""The users module -- ``client.users``.

Manage FortiSOAR users (People records + auth credentials) via ``/api/3/people``.
Each user is two linked records -- a **People** profile and an internal **auth
user** -- which ``/api/3/people`` creates atomically when the ``user`` and
``roles`` keys are supplied. Roles and teams may be given as UUIDs or friendly
names; names are resolved via a per-instance cache populated on first use.

.. note::
    Not to be confused with ``/api/auth/users`` -- that is the **API-key user**
    surface (key material + lifecycle, wrapped by
    :class:`~pyfsr.api.api_users.ApiKeyUsersAPI` as ``client.api_users``).
    ``/api/3/people`` here is the **People module**: a Hydra collection of
    Person records (human profiles with name, email, department, roles,
    teams). The two are not aliases -- different paths, different record
    shapes (Hydra vs ``{"usersresp": [...]}``), different concepts.
"""

from __future__ import annotations

from typing import Any, cast

from ..models import Role, Team, User
from ..pagination import extract_members
from ..utils.validation import is_uuid as _is_uuid
from .base import BaseAPI


class UsersAPI(BaseAPI):
    """
    Manage FortiSOAR users (People records + auth credentials) via ``/api/3/people``.

    Each FortiSOAR user has two linked records:
    - A **People** record (profile -- name, email, department, …)
    - An **auth user** record (login credentials, managed internally by the auth service)

    The ``/api/3/people`` endpoint creates both atomically when the ``user`` and
    ``roles`` keys are supplied in the payload.

    Roles and teams can be specified as UUIDs **or** friendly names -- the API resolves
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
                Set for real via the admin reset endpoint after creation, because
                the People POST alone leaves the das login without a password.
            firstname: First name.
            lastname: Last name.
            email: Email address.
            roles: Role UUIDs **or** friendly names (e.g. ``["SOC Analyst"]``).
                At least one required.
            access_type: ``"Named"`` (default) or ``"Concurrent"``.
            active: Whether the account is active on creation. Defaults to ``True``.
                Maps to the nested ``user.status`` (1=active, 2=inactive) that das
                requires; omitting it made das default the login to inactive.
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
                # das provisions the login from this nested object and defaults
                # the account to status 2 (INACTIVE) when `status` is omitted --
                # cyops-api PeopleSubscriber::handlePeoplePreCreate, live-verified
                # on 8.0.0. Without this the created user cannot log in ("User is
                # either inactive or locked"). 1 = active, 2 = inactive.
                "status": 1 if active else 2,
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

        # The People POST does NOT set the das login password -- cyops-api's
        # create flow (PeopleSubscriber::handlePeopleCreate) only generates a
        # reset token and emails it, so the nested user.password above is inert
        # and the account logs in with "Password not set ... reset first"
        # (live-verified 8.0.0). Set it explicitly via the admin reset endpoint
        # so the created user can actually authenticate. This requires the
        # caller to be an admin other than the new user (das forbids self-reset);
        # if it fails we surface a clear error rather than returning a user who
        # silently cannot log in.
        if password:
            try:
                self.client.post(
                    "/api/3/resetpassword",
                    data={"loginId": loginid, "password": password, "confirmPassword": password},
                )
            except Exception as exc:  # noqa: BLE001 - re-raise with context
                from ..exceptions import APIError

                raise APIError(
                    f"user {loginid!r} was created but setting its password failed "
                    f"({exc}); the account exists but cannot log in until an admin "
                    f"resets its password (POST /api/3/resetpassword)."
                ) from exc

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

        Looks up by ``email`` (the filterable unique key on ``/api/3/people`` --
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
            email: Email address -- the lookup key and the create-time email.
            roles: Role UUIDs or friendly names. Used only on the create path.
            **kwargs: Additional :meth:`create` arguments (``access_type``,
                ``active``, ``department``, ``teams``, etc.).

        Returns:
            ``(User, created)`` -- the existing user with ``created=False``, or
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

        Doctest:

            >>> from pyfsr._testing import demo_client
            >>> client = demo_client()
            >>> users = client.users.list()
            >>> len(users)
            2
            >>> users[0].firstname
            'CS'
            >>> users[0].lastname
            'Admin'
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

    def _set_active(self, user: str, active: bool, typed: bool) -> User | dict[str, Any]:
        """Activate/deactivate ``user`` (login ID or People UUID) the way the UI does.

        1. ``PUT /api/auth/users`` with ``{"update": {uuid, status, user_type,
           access_type}}`` sets the das login ``status`` (1=active, 2=inactive).
           This is what allows or blocks login. The body must carry the current
           ``user_type``/``access_type`` inside the ``update`` wrapper; a bare
           ``{uuid, status}`` returns 200 but changes nothing.
        2. ``csActive`` on the People record, which is what the UI displays.
        """
        if _is_uuid(user):
            person_uuid = user
            person = cast(dict[str, Any], self.client.get(f"/api/3/people/{person_uuid}"))
            user_id = person.get("userId")
            if not user_id:
                raise LookupError(f"People record {person_uuid} has no linked login (userId)")
        else:
            ids = self.lookup(user)
            person_uuid, user_id = ids["person_uuid"], ids["user_id"]
        das = cast(dict[str, Any], self.client.get("/api/auth/users", params={"uuid": user_id}))
        users = das.get("usersresp") or []
        if not users:
            raise LookupError(f"no das user for userId {user_id!r}")
        self.client.put(
            "/api/auth/users",
            data={
                "update": {
                    "uuid": user_id,
                    "status": 1 if active else 2,
                    "user_type": users[0]["user_type"],
                    "access_type": users[0]["access_type"],
                }
            },
        )
        return self.update(person_uuid, csActive=active, typed=typed)

    def deactivate(self, user: str, *, typed: bool = True) -> User | dict[str, Any]:
        """
        Deactivate a user account, as the UI does.

        New logins are refused (``Invalid credentials or account locked``) and
        the user's existing tokens stop working within seconds. Setting
        ``csActive=False`` alone (e.g. via :meth:`update`) does **not** do this;
        it only changes how the UI shows the user.

        Args:
            user: the login ID (username) or the People UUID.
            typed: parse the result into a :class:`~pyfsr.models.User` (default);
                pass ``False`` for the raw dict.

        Returns:
            The updated People record.

        Raises:
            LookupError: no such login, or no login linked to the People record.

        Example:
            >>> client.users.deactivate("jsmith")
        """
        return self._set_active(user, False, typed)

    def activate(self, user: str, *, typed: bool = True) -> User | dict[str, Any]:
        """
        Reactivate a user account; the reverse of :meth:`deactivate`.

        Args:
            user: the login ID (username) or the People UUID.
            typed: parse the result into a :class:`~pyfsr.models.User` (default);
                pass ``False`` for the raw dict.

        Returns:
            The updated People record.

        Raises:
            LookupError: no such login, or no login linked to the People record.
        """
        return self._set_active(user, True, typed)

    def lookup(self, loginid: str) -> dict[str, Any]:
        """Resolve a login ID to its das user UUID and People UUID.

        ``GET /api/auth/users?loginid=`` gives the das user; ``GET
        /api/3/people?userId=`` gives the People record linked to it.

        Returns:
            ``{"loginid", "user_id", "person_uuid"}``.

        Raises:
            LookupError: no such login, or no People record linked to it.
        """
        users = self.client.get("/api/auth/users", params={"loginid": loginid}).get("usersresp") or []
        if not users:
            raise LookupError(f"no user with login ID {loginid!r}")
        user_id = users[0]["uuid"]
        people = extract_members(self.client.get("/api/3/people", params={"userId": user_id}))
        if not people:
            raise LookupError(f"no People record for login ID {loginid!r} (das user {user_id})")
        return {"loginid": loginid, "user_id": user_id, "person_uuid": people[0]["uuid"]}

    def reset_password(self, loginid: str, new_password: str, *, send_email: bool = False) -> Any:
        """Admin reset of another user's application password (``POST /api/3/resetpassword``).

        Sends the same body as the UI's *Reset Password* dialog::

            {"uuid": <People UUID>, "currentUUID": <das user UUID>,
             "loginId": ..., "password": ..., "sendEmail": false}

        Both UUIDs are looked up from ``loginid`` (see :meth:`lookup`). The
        server returns the string ``"Password Changed sucessfully"`` (sic).

        das refuses resetting your own login this way -- use
        :meth:`change_password`. This changes the **application** login only;
        the appliance's OS account of the same name (e.g. ``csadmin`` over SSH)
        is a separate credential -- see
        :meth:`pyfsr.appliance.HostNamespace.set_os_password`.

        Args:
            loginid: Login ID of the user whose password is reset.
            new_password: The new password; must pass the password policy and history check.
            send_email: Ask FortiSOAR to email a notice of the reset.

        Returns:
            The response body.
        """
        ids = self.lookup(loginid)
        return self.client.post(
            "/api/3/resetpassword",
            data={
                "uuid": ids["person_uuid"],
                "currentUUID": ids["user_id"],
                "loginId": loginid,
                "password": new_password,
                "sendEmail": send_email,
            },
        )

    def whoami(self) -> dict[str, Any]:
        """Identify the calling user, whatever the client authenticated with.

        Combines ``GET /api/3/actors/current`` (the People profile) with
        ``GET /api/auth/users?uuid=<userId>`` (the das login record). The login
        ID is the username typed on the login page.

        Returns:
            ``{"loginid", "person_uuid", "user_id", "email", "name"}`` --
            ``person_uuid`` is the People UUID, ``user_id`` the das user UUID.
        """
        actor = self.client.get("/api/3/actors/current")
        users = self.client.get("/api/auth/users", params={"uuid": actor["userId"]}).get("usersresp") or []
        if not users:
            raise LookupError(f"no das user for userId {actor['userId']!r}")
        return {
            "loginid": users[0]["loginid"],
            "person_uuid": actor["uuid"],
            "user_id": actor["userId"],
            "email": actor.get("email"),
            "name": " ".join(filter(None, (actor.get("firstname"), actor.get("lastname")))),
        }

    def change_password(self, old_password: str, new_password: str) -> dict[str, Any]:
        """Change the calling user's own application password (``PUT /api/3/changepassword``).

        Only the two passwords are needed: the People UUID and login ID the
        endpoint also requires are looked up with :meth:`whoami`. When the client
        authenticated with a username/password, its stored password is updated
        so token refreshes keep working (the UI logs out instead).

        Server-side rules (cyops-api + das, 8.0.1): the body needs ``uuid``,
        ``loginId``, ``oldPassword`` and ``newPassword``; ``uuid`` and
        ``loginId`` must be the caller's own; the new password must differ from
        the old one and pass the password policy. LDAP/SAML users cannot change
        their password here ("Password change is not allowed for this user").

        Args:
            old_password: The current password.
            new_password: The new password.

        Returns:
            The raw response body.
        """
        if new_password == old_password:
            raise ValueError("new password can not be same as old password")
        me = self.whoami()
        resp = self.client.put(
            "/api/3/changepassword",
            data={
                "uuid": me["person_uuid"],
                "loginId": me["loginid"],
                "oldPassword": old_password,
                "newPassword": new_password,
            },
        )
        auth = getattr(self.client, "auth", None)
        if getattr(auth, "username", None) == me["loginid"] and hasattr(auth, "password"):
            auth.password = new_password
        return resp

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
