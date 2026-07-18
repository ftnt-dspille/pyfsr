"""Team CRUD (``/api/3/teams``).

A FortiSOAR **team** owns records (the ``owners`` relationship) and scopes
visibility. Teams are their own ``/api/3/teams`` collection — first-class on the
client as ``client.teams`` ��� and accept uuids **or** friendly names anywhere a
team is referenced, resolved via a per-instance cache populated on first use.

Reads come back as typed :class:`~pyfsr.models.Team` records (dict-compatible,
so ``t["name"]`` / ``t.get("uuid")`` keep working). The role/team name→uuid
resolution helpers here are the single source of truth —
:meth:`pyfsr.api.users.UsersAPI.list_teams` and friends delegate to this class.

Example:
    >>> team = client.teams.create("Tier 1 SOC", description="front-line triage")
    >>> [t.name for t in client.teams.list() if t.description]
    ['Tier 1 SOC']
"""

from __future__ import annotations

from typing import Any

from ..models import Team
from ..pagination import extract_members
from ..utils.validation import is_uuid as _is_uuid
from .base import BaseAPI

_BASE = "/api/3/teams"


class TeamsAPI(BaseAPI):
    """Team discovery and creation (``/api/3/teams``)."""

    _team_cache: dict[str, Team] | None = None  # name -> record

    def _team_by_name(self) -> dict[str, Team]:
        if self._team_cache is None:
            self._team_cache = {t["name"]: t for t in self.list() if t.get("name")}
        return self._team_cache

    def _resolve_team_uuid(self, team: str) -> str:
        """Accept a team uuid or name and return the uuid."""
        if _is_uuid(team):
            return team.strip()
        record = self._team_by_name().get(team)
        if not record:
            raise ValueError(f"team {team!r} not found; call list() to see available teams")
        return record["uuid"]

    # ------------------------------------------------------ name/uuid resolution
    def team_map(self) -> dict[str, str]:
        """Return ``{name: uuid}`` for all teams, cached for the instance lifetime."""
        return {name: t["uuid"] for name, t in self._team_by_name().items()}

    def team_uuid_by_name(self, name: str) -> str | None:
        """Look up a team UUID by display name (case-sensitive); ``None`` if absent."""
        return self.team_map().get(name)

    def _resolve_teams(self, teams: list[str]) -> list[str]:
        """Accept team UUIDs or names; return UUIDs. Raises ``ValueError`` for unknown names."""
        return [self._resolve_team_uuid(t) for t in teams]

    # ------------------------------------------------------------------- read
    def list(self, params: dict[str, Any] | None = None) -> list[Team]:
        """List all teams (``GET /api/3/teams``) as typed :class:`~pyfsr.models.Team` records.

        Doctest:

            >>> from pyfsr._testing import demo_client
            >>> client = demo_client()
            >>> teams = client.teams.list()
            >>> len(teams)
            1
            >>> teams[0].name
            'SOC Team'
        """
        return [Team.model_validate(m) for m in extract_members(self.client.get(_BASE, params=params))]

    def get(self, team: str) -> Team:
        """Fetch one team by uuid or name as a :class:`~pyfsr.models.Team`."""
        uuid = self._resolve_team_uuid(team)
        return Team.model_validate(self.client.get(f"{_BASE}/{uuid}"))

    # ------------------------------------------------------------------ write
    def create(self, name: str, *, description: str | None = None) -> Team:
        """Create a team (``POST /api/3/teams``).

        Only ``name`` is required; ``description`` is optional. The server fills
        in the relationship arrays (``actors``/``parents``/``children``/
        ``siblings``) as empty lists on creation.
        """
        body: dict[str, Any] = {"name": name}
        if description is not None:
            body["description"] = description
        team = Team.model_validate(self.client.post(_BASE, data=body))
        self._team_cache = None  # name→record cache is now stale
        return team

    def get_or_create(
        self,
        name: str,
        *,
        description: str | None = None,
    ) -> tuple[Team, bool]:
        """Idempotently ensure team ``name`` exists; return ``(team, created)``.

        If a team with that name already exists, it is returned unchanged (its
        ``description`` is **not** modified). Returns ``created=True`` only when
        the team was newly created.
        """
        existing = self.team_uuid_by_name(name)
        if existing is not None:
            return self.get(name), False
        return self.create(name, description=description), True

    def update(self, team: str, *, name: str | None = None, description: str | None = None) -> Team:
        """Update a team's ``name`` and/or ``description`` (``PUT /api/3/teams/<uuid>``).

        ``team`` is a uuid or current name. Pass at least one of ``name`` /
        ``description``; only the given fields are sent (a partial update), and
        the full updated :class:`~pyfsr.models.Team` is returned.
        """
        uuid = self._resolve_team_uuid(team)
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if not body:
            raise ValueError("update() needs at least one of name= or description=")
        updated = Team.model_validate(self.client.put(f"{_BASE}/{uuid}", data=body))
        self._team_cache = None  # a renamed team invalidates the name→record cache
        return updated

    def delete(self, team: str) -> None:
        """Delete a team (``DELETE /api/3/teams/<uuid>``). ``team`` is a uuid or name."""
        uuid = self._resolve_team_uuid(team)
        self.client.delete(f"{_BASE}/{uuid}")
        self._team_cache = None
