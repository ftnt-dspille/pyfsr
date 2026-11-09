"""pyfsr.schema: one cached schema per instance, and the pre-write check built on it."""

from __future__ import annotations

import os
import time

import pytest
from pydantic import ValidationError as PydanticValidationError

from pyfsr.exceptions import ValidationError
from pyfsr.records import RecordSet
from pyfsr.schema import SchemaCache, clear_disk

PICK_UUID = "11111111-1111-1111-1111-111111111111"
PERSON = "/api/3/people/22222222-2222-2222-2222-222222222222"
ALERT_IRI = "/api/3/alerts/33333333-3333-3333-3333-333333333333"


def _attr(name, form, typ="string", **kw):
    a = {
        "name": name,
        "formType": form,
        "type": typ,
        "writeable": kw.pop("writeable", True),
        "collection": kw.pop("collection", False),
        "validation": {"required": kw.pop("required", False), "maxlength": kw.pop("maxlength", 10485761)},
        "defaultValue": kw.pop("default", None),
    }
    a.update(kw)
    return a


def _module(type_, *, ownable, attrs):
    return {"type": type_, "module": type_, "ownable": ownable, "queueable": True, "attributes": attrs}


ALERTS = _module(
    "alerts",
    ownable=False,
    attrs=[
        _attr("name", "text", required=True, maxlength=255),
        _attr("description", "richtext"),
        _attr("emailFrom", "email", maxlength=10),
        _attr(
            "severity",
            "picklist",
            "picklists",
            dataSource={"model": "picklists", "query": {"filters": [{"field": "listName__name", "value": "Severity"}]}},
        ),
        _attr("assignedTo", "lookup", "people", dataSource={"model": "people"}),
        _attr("seenOn", "datetime", "integer"),
        _attr("sourceId", "text", writeable=False),
    ],
)
COMMENTS = _module("comments", ownable=True, attrs=[_attr("content", "richtext")])


class _Picklists:
    def values(self, name):
        assert name == "Severity"
        return [
            {"itemValue": "High", "uuid": PICK_UUID, "iri": f"/api/3/picklists/{PICK_UUID}"},
            {"itemValue": "Low", "uuid": "44444444-4444-4444-4444-444444444444", "iri": None},
        ]


class _Client:
    def __init__(self, base_url="https://box-a.example", modules=None):
        self.base_url = base_url
        self.modules_meta = {m["type"]: m for m in (modules or [ALERTS, COMMENTS])}
        self.calls: list[dict] = []
        self.picklists = _Picklists()
        self.schema = SchemaCache(self)

    def get(self, path, params=None, **kw):
        params = params or {}
        self.calls.append(params)
        want = params.get("type") or params.get("module")
        members = [m for t, m in self.modules_meta.items() if want is None or t == want]
        return {"hydra:member": members}


@pytest.fixture
def disk(monkeypatch, tmp_path):
    monkeypatch.setenv("PYFSR_SCHEMA_TTL", "600")
    monkeypatch.setenv("PYFSR_SCHEMA_CACHE_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- caching
def test_a_loop_of_creates_fetches_the_schema_once():
    c = _Client()
    for i in range(50):
        assert c.schema.check("alerts", {"name": f"a{i}"}).ok
    assert c.schema.fetches == 1


def test_a_second_process_reuses_the_disk_cache(disk):
    _Client().schema.get("alerts")
    other = _Client()
    assert other.schema.get("alerts").type == "alerts"
    assert other.schema.fetches == 0


def test_an_expired_disk_entry_is_refetched(disk, monkeypatch):
    _Client().schema.get("alerts")
    old = time.time() - 3600
    os.utime(disk / _Client().schema._dir.name / "alerts.json", (old, old))
    other = _Client()
    other.schema.get("alerts")
    assert other.schema.fetches == 1


def test_instances_never_share_a_cache(disk):
    _Client("https://box-a.example").schema.get("alerts")
    b = _Client("https://box-b.example", modules=[_module("alerts", ownable=True, attrs=[])])
    assert b.schema.get("alerts").ownable is True
    assert b.schema.fetches == 1


def test_switching_instance_clears_and_rewarms_that_box(disk):
    _Client("https://box-a.example").schema.warm(["alerts", "comments"])
    _Client("https://box-b.example").schema.get("alerts")  # switch away
    back = _Client("https://box-a.example")
    back.schema.get("alerts")  # first use after switching back
    # the stale disk entries were dropped and both modules fetched again, eagerly
    assert back.schema.fetches == 2
    assert back.schema.is_cached("comments")


def test_staying_on_one_instance_does_not_rewarm(disk):
    _Client().schema.get("alerts")
    again = _Client()
    again.schema.get("alerts")
    assert again.schema.fetches == 0


def test_warming_many_modules_uses_one_full_fetch():
    mods = [_module(f"m{i}", ownable=True, attrs=[]) for i in range(8)]
    c = _Client(modules=mods)
    found = c.schema.warm([m["type"] for m in mods])
    assert all(found.values()) and c.schema.fetches == 1


def test_warming_a_few_modules_fetches_each():
    c = _Client()
    c.schema.warm(["alerts", "comments"])
    assert c.schema.fetches == 2


def test_a_missing_module_is_remembered_not_refetched():
    c = _Client()
    assert c.schema.get("nosuch") is None
    assert c.schema.get("nosuch") is None
    assert c.schema.fetches == 2  # type= then module= once, never again


def test_clear_drops_memory_and_disk(disk):
    c = _Client()
    c.schema.get("alerts")
    assert c.schema.clear() == 1
    c.schema.get("alerts")
    assert c.schema.fetches == 2


def test_clear_disk_all_instances(disk):
    _Client("https://box-a.example").schema.get("alerts")
    _Client("https://box-b.example").schema.get("alerts")
    assert clear_disk(None) == 2


# ---------------------------------------------------------------- checks
def _codes(result):
    return {p.code for p in result.problems}


def test_owners_on_a_module_that_is_not_ownable_is_named_not_a_permission_error():
    r = _Client().schema.check("alerts", {"name": "x", "owners": ["/api/3/teams/t"]})
    (p,) = r.problems
    assert p.code == "not_ownable" and "not team-ownable" in p.message and "not a permission problem" in p.message


def test_owners_on_an_ownable_module_passes():
    assert _Client().schema.check("comments", {"content": "x", "owners": ["/api/3/teams/t"]}).ok


def test_required_fields_are_checked_on_create_only():
    c = _Client()
    assert _codes(c.schema.check("alerts", {"description": "x"})) == {"required"}
    assert c.schema.check("alerts", {"description": "x"}, op="update").ok


def test_too_long_read_only_and_unknown_are_all_reported_at_once():
    r = _Client().schema.check("alerts", {"name": "x", "emailFrom": "a@b.example.com", "sourceId": "s", "nmae": 1})
    assert _codes(r) == {"too_long", "read_only", "unknown_field"}
    assert "did you mean 'name'" in next(p.message for p in r.problems if p.code == "unknown_field")


def test_picklist_value_not_in_the_list_names_the_valid_ones():
    r = _Client().schema.check("alerts", {"name": "x", "severity": "Hgh"})
    (p,) = r.problems
    assert p.code == "picklist" and "valid: High, Low" in p.message


def test_picklist_friendly_value_and_known_iri_pass():
    c = _Client()
    assert c.schema.check("alerts", {"name": "x", "severity": "high"}).ok
    assert c.schema.check("alerts", {"name": "x", "severity": f"/api/3/picklists/{PICK_UUID}"}).ok


def test_relation_to_the_wrong_module_is_refused():
    c = _Client()
    assert c.schema.check("alerts", {"name": "x", "assignedTo": PERSON}).ok
    (p,) = c.schema.check("alerts", {"name": "x", "assignedTo": ALERT_IRI}).problems
    assert p.code == "relation" and "links to 'people'" in p.message


def test_value_rules_still_convert():
    r = _Client().schema.check("alerts", {"name": "x", "seenOn": "2026-01-02T03:04:05Z"})
    assert r.ok and isinstance(r.data["seenOn"], int) and r.conversions


def test_a_field_added_on_the_box_refetches_a_cached_schema_once():
    c = _Client()
    c.schema.get("alerts")
    c.modules_meta["alerts"] = {**ALERTS, "attributes": [*ALERTS["attributes"], _attr("newField", "text")]}
    r = c.schema.check("alerts", {"name": "x", "newField": "y"})
    assert r.ok and r.refetched and c.schema.fetches == 2


def test_an_unknown_module_passes_through_unchecked():
    r = _Client().schema.check("nosuch", {"anything": 1})
    assert r.ok and r.data == {"anything": 1}


def test_record_set_refuses_owners_before_sending():
    c = _Client()
    with pytest.raises(ValidationError, match="not team-ownable"):
        RecordSet(c, "alerts").prepare_values({"name": "x", "owners": ["/api/3/teams/t"]}, op="update")


# ---------------------------------------------------------------- pydantic model
def test_record_model_requires_on_create_and_forbids_unknown_keys():
    s = _Client().schema.get("alerts")
    Create = s.record_model(op="create")
    with pytest.raises(PydanticValidationError):
        Create()
    with pytest.raises(PydanticValidationError):
        Create(name="x", nmae="y")
    assert Create(name="x", uuid="u").name == "x"
    Update = s.record_model()
    assert Update(description="d").description == "d"
    with pytest.raises(PydanticValidationError):
        Update(emailFrom="x" * 11)


# ---------------------------------------------------------------- publish clears
def test_publish_clears_the_schema_cache_even_when_it_fails(monkeypatch):
    from pyfsr.api.modules_admin import ModulesAdminAPI

    c = _Client()
    c.schema.get("alerts")
    admin = ModulesAdminAPI(c)

    def boom(**kw):
        raise RuntimeError("publish failed")

    monkeypatch.setattr(admin, "_publish", boom)
    with pytest.raises(RuntimeError):
        admin.publish()
    assert not c.schema.is_cached("alerts")


# ---------------------------------------------------------------- cache paths
@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "x.json", ""])
def test_unsafe_module_names_never_touch_the_disk(disk, bad):
    c = _Client()
    assert c.schema._cache_file(bad) is None
    assert c.schema._from_disk(bad) is None


def test_a_server_type_with_path_characters_is_kept_in_memory_only(disk):
    evil = _module("../../escape", ownable=True, attrs=[_attr("x", "text")])
    c = _Client(modules=[evil])
    assert c.schema.get("../../escape") is not None  # usable in memory
    assert not any(disk.rglob("*escape*"))
    assert c.schema.cached_modules() == []


def test_a_member_without_attributes_is_not_a_schema():
    from pyfsr.schema import _pick

    summary = {"type": "incidents", "module": "incidents"}
    assert _pick([summary], "incidents") is None
    assert _pick([{**summary, "attributes": []}], "incidents") is not None


def test_demo_clients_never_touch_the_disk_cache(tmp_path, monkeypatch):
    from pyfsr._testing import demo_client

    monkeypatch.setenv("PYFSR_SCHEMA_CACHE_DIR", str(tmp_path))
    c = demo_client()
    c.schema.get("alerts")
    assert not any(tmp_path.iterdir())
