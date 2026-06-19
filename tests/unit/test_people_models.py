"""Unit tests for the user/team/role models and BaseRecord relationship accessors."""

from pyfsr.models import FileRecord, Role, Team, User, model_for
from pyfsr.models._generated import Alert, Task

# A People/Person record as returned by /api/3/people (single-relation expansion
# of createUser/modifyUser uses this same shape).
_PERSON = {
    "@id": "/api/3/people/3451141c-bac6-467c-8d72-85e0fab569ce",
    "@type": "Person",
    "firstname": "CS",
    "lastname": "Admin",
    "title": "Admin",
    "email": "admin@example.com",
    "phoneWork": "+16462759691",
    "csActive": True,
    "createDate": 1722524616.88522,
    "uuid": "3451141c-bac6-467c-8d72-85e0fab569ce",
}


def test_model_registry_maps_people_teams_roles():
    assert model_for("people") is User
    assert model_for("teams") is Team
    assert model_for("roles") is Role
    assert model_for("files") is FileRecord


def test_user_typed_fields_and_display_name():
    u = User.model_validate(_PERSON)
    assert u.firstname == "CS"
    assert u.email == "admin@example.com"
    assert u.name == "CS Admin"
    assert u.iri == _PERSON["@id"]
    assert u["@id"] == _PERSON["@id"]  # dict-compat shim
    # epoch with sub-second precision stays a float
    assert isinstance(u.createDate, float)


def test_team_and_role_slim_schema_no_extra_leak():
    team = Team.model_validate({"@id": "/api/3/teams/t1", "@type": "Team", "name": "SOC Team"})
    assert team.name == "SOC Team"
    assert team.__pydantic_extra__ == {}
    role = Role.model_validate(
        {
            "@id": "/api/3/roles/r1",
            "@type": "Role",
            "name": "SOC Manager",
            "modulePermissions": [1, 2],
        }
    )
    assert role.name == "SOC Manager"
    assert len(role.modulePermissions) == 2


def test_create_modify_user_accessors_expanded():
    alert = Alert.model_validate(
        {"@id": "/api/3/alerts/a1", "@type": "Alert", "createUser": _PERSON, "modifyUser": _PERSON}
    )
    assert isinstance(alert.create_user, User)
    assert alert.create_user.name == "CS Admin"
    assert isinstance(alert.modify_user, User)


def test_user_accessor_handles_bare_iri():
    iri = "/api/3/people/3451141c-bac6-467c-8d72-85e0fab569ce"
    alert = Alert.model_validate({"@id": "/api/3/alerts/a1", "@type": "Alert", "createUser": iri})
    assert isinstance(alert.create_user, User)
    assert alert.create_user.iri == iri
    assert alert.create_user.name is None  # thin: only @id is known


def test_assigned_to_falls_back_to_assigned_to_person():
    task = Task.model_validate(
        {"@id": "/api/3/tasks/t1", "@type": "Task", "assignedToPerson": _PERSON}
    )
    assert isinstance(task.assigned_to, User)
    assert task.assigned_to.email == "admin@example.com"


def test_assigned_to_none_when_absent():
    alert = Alert.model_validate({"@id": "/api/3/alerts/a1", "@type": "Alert"})
    assert alert.assigned_to is None
    assert alert.create_user is None
    assert alert.owner_teams == []


def test_owner_teams_to_many_accessor():
    alert = Alert.model_validate(
        {
            "@id": "/api/3/alerts/a1",
            "@type": "Alert",
            "owners": [
                {"@id": "/api/3/teams/t1", "@type": "Team", "name": "SOC Team"},
                "/api/3/teams/t2",
            ],
        }
    )
    teams = alert.owner_teams
    assert [t.iri for t in teams] == ["/api/3/teams/t1", "/api/3/teams/t2"]
    assert teams[0].name == "SOC Team"


def test_file_record_upload_shape():
    rec = FileRecord.model_validate(
        {
            "@id": "/api/3/files/f1",
            "@type": "File",
            "filename": "x.zip",
            "mimeType": "application/zip",
        }
    )
    assert rec.filename == "x.zip"
    assert rec.iri == "/api/3/files/f1"
    assert rec["@id"] == "/api/3/files/f1"  # import_config relies on this
