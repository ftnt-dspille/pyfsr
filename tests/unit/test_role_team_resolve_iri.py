"""Role and team resolvers take an IRI as well as a uuid or name.

``api_keys.create(roles=..., teams=...)`` documents IRIs, and a person's
``roles`` / ``teams`` come back as IRIs, so passing them straight through must work.
"""

from pyfsr.api.roles import RolesAPI
from pyfsr.api.teams import TeamsAPI

UUID = "21d7a7c6-9d68-438f-b732-e8950c5f745b"


def test_role_iri_resolves_without_a_lookup():
    assert RolesAPI(object())._resolve_roles([f"/api/3/roles/{UUID}"]) == [UUID]


def test_team_iri_resolves_without_a_lookup():
    assert TeamsAPI(object())._resolve_teams([f"/api/3/teams/{UUID}"]) == [UUID]
