"""Unit tests for record projection / summarization."""

from pyfsr import Incident, project, project_record, to_jsonable
from pyfsr.pagination import HydraPage
from pyfsr.projection import SUMMARY_FIELDS


def _page(members, total=2, limit=30):
    return HydraPage(members=members, total=total, page=1, limit=limit, raw={})


# -- to_jsonable ------------------------------------------------------------
def test_to_jsonable_model_to_dict():
    inc = Incident.model_validate({"@id": "/api/3/incidents/u1", "uuid": "u1", "name": "x"})
    out = to_jsonable(inc)
    assert isinstance(out, dict)
    assert out["@id"] == "/api/3/incidents/u1"
    assert out["name"] == "x"


def test_to_jsonable_page_envelope():
    out = to_jsonable(_page([{"uuid": "a"}, {"uuid": "b"}]))
    assert out == {
        "members": [{"uuid": "a"}, {"uuid": "b"}],
        "total": 2,
        "page": 1,
        "has_next": False,
    }


def test_to_jsonable_passthrough_scalar():
    assert to_jsonable(7) == 7
    assert to_jsonable("x") == "x"


# -- project_record ---------------------------------------------------------
def test_project_record_fields_keeps_refs():
    rec = {"@id": "/api/3/x/1", "uuid": "1", "name": "n", "severity": "High", "extra": "drop"}
    out = project_record(rec, fields=["name"])
    assert out == {"name": "n", "@id": "/api/3/x/1", "uuid": "1"}
    assert "extra" not in out


def test_project_record_summary_collapses_picklist():
    rec = {
        "uuid": "1",
        "name": "n",
        "severity": {"itemValue": "High", "@id": "/api/3/picklists/sev"},
        "status": {"@id": "/api/3/picklists/open"},
        "audit": {"big": "payload"},
    }
    out = project_record(rec, summary=True)
    assert out["severity"] == "High"  # itemValue wins
    assert out["status"] == "/api/3/picklists/open"  # @id fallback
    assert "audit" not in out  # not a summary field


def test_project_record_summary_guarantees_reference():
    out = project_record({"uuid": "only-id", "weird": 1}, summary=True)
    assert out == {"uuid": "only-id"}


def test_project_record_no_opts_returns_full_dict():
    inc = Incident.model_validate({"uuid": "u1", "name": "x"})
    out = project_record(inc)
    assert out["uuid"] == "u1" and out["name"] == "x"


def test_project_record_non_dict_passthrough():
    assert project_record("scalar", summary=True) == "scalar"


def test_summary_fields_includes_identity():
    assert "uuid" in SUMMARY_FIELDS and "name" in SUMMARY_FIELDS


# -- project (page-aware) ---------------------------------------------------
def test_project_page_members_trimmed():
    page = _page([{"uuid": "a", "name": "x", "junk": 1}, {"uuid": "b", "name": "y", "junk": 2}])
    out = project(page, fields=["name"])
    assert out["total"] == 2
    assert out["members"] == [
        {"name": "x", "uuid": "a"},
        {"name": "y", "uuid": "b"},
    ]


def test_project_list_of_records():
    out = project([{"uuid": "a", "name": "x", "j": 1}], summary=True)
    assert out == [{"uuid": "a", "name": "x"}]


def test_project_no_opts_is_jsonable():
    page = _page([{"uuid": "a"}])
    assert project(page) == to_jsonable(page)


# -- iri_to_uuid ------------------------------------------------------------
from pyfsr import iri_to_uuid  # noqa: E402


def test_iri_to_uuid_from_iri_string():
    assert iri_to_uuid("/api/3/alerts/abc-123") == "abc-123"


def test_iri_to_uuid_from_bare_uuid_string():
    assert iri_to_uuid("u-1") == "u-1"


def test_iri_to_uuid_prefers_atid_over_uuid_key():
    assert iri_to_uuid({"@id": "/api/3/alerts/from-iri", "uuid": "from-uuid"}) == "from-iri"


def test_iri_to_uuid_falls_back_to_uuid_then_id():
    assert iri_to_uuid({"uuid": "u-9"}) == "u-9"
    assert iri_to_uuid({"id": "i-9"}) == "i-9"


def test_iri_to_uuid_from_model():
    inc = Incident(name="x")  # BaseRecord; no @id/uuid set -> None
    assert iri_to_uuid(inc) is None


def test_iri_to_uuid_none_and_empty():
    assert iri_to_uuid(None) is None
    assert iri_to_uuid({}) is None
    assert iri_to_uuid("") is None
    assert iri_to_uuid(123) is None
