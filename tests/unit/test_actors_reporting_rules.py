"""Unit tests for the typed resolver APIs — ``client.actors`` / ``.reporting`` / ``.rules``.

These are the SDK surfaces the config-export resolvers route through instead of
raw ``client.get`` calls. Fixtures below are trimmed from **live 8.0.0 responses**
(per the ``types-from-live-wire`` doctrine), so the quirks they encode are real:

- ``/api/3/actors`` is an aggregate spanning subtypes, takes no ``title`` server
  filter, and titles are **not unique** (a live box carries two distinct Person
  actors both titled ``"Admin"``).
- ``Appliance`` actors carry no ``title`` key at all — they're named by ``name``.
- The rule engine answers at ``/rule/api/`` on some builds and ``/api/rule/api/``
  on others; the wrong root falls through to the SPA and fails JSON parsing.
- Reports are identified by ``displayName``; there is no ``name`` field.
"""

from types import SimpleNamespace

import pytest

from pyfsr.api.actors import ActorsAPI
from pyfsr.api.reporting import ReportingAPI
from pyfsr.api.rules import RulesAPI
from pyfsr.models import Appliance, DeliveryRule, PreprocessingRule, Report, RuleChannel, User


class FakeClient:
    """Minimal client recording calls and delegating to a handler."""

    def __init__(self, handler=None):
        self.calls = []
        self._handler = handler or (lambda *a, **k: {})
        self.auth = SimpleNamespace(check_operation_supported=lambda operation=None: None)

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append(("GET", url, params))
        return self._handler("GET", url, params=params)


# Two Person actors sharing a title + two Appliances with no title key at all —
# exactly what a live 8.0.0 box returns from /api/3/actors.
_ACTORS = [
    {"@id": "/api/3/people/aaa", "@type": "Person", "title": "Admin", "uuid": "aaa", "email": "admin@example.com"},
    {"@id": "/api/3/people/bbb", "@type": "Person", "title": "Admin", "uuid": "bbb", "email": "admin@example.com"},
    {"@id": "/api/3/appliances/ccc", "@type": "Appliance", "name": "Playbook", "uuid": "ccc"},
    {"@id": "/api/3/api_keys/ddd", "@type": "ApiKey", "name": "svc-key", "uuid": "ddd"},
]


def _actors_api(members=None):
    c = FakeClient(lambda m, u, **k: {"hydra:member": _ACTORS if members is None else members})
    return ActorsAPI(c), c


# ------------------------------------------------------------------------ actors


def test_actors_list_parses_each_subtype_by_at_type():
    api, _ = _actors_api()
    actors = api.list()
    assert [type(a).__name__ for a in actors] == ["User", "User", "Appliance", "ApiKey"]
    assert isinstance(actors[0], User)
    assert isinstance(actors[2], Appliance)


def test_actors_list_untyped_returns_raw_dicts():
    api, _ = _actors_api()
    assert all(isinstance(a, dict) for a in api.list(typed=False))


def test_appliance_actor_title_reads_as_none_not_attribute_error():
    # The shared actors table has a title column but Appliance rows omit the key;
    # reading .title across the union must not raise.
    api, _ = _actors_api()
    appliance = api.list()[2]
    assert appliance.title is None
    assert appliance.name == "Playbook"


def test_actors_get_matches_title_exactly():
    api, _ = _actors_api()
    assert api.get("Admin").uuid == "aaa"


def test_actors_get_is_case_sensitive():
    api, _ = _actors_api()
    with pytest.raises(ValueError, match="actor 'admin' not found"):
        api.get("admin")


def test_actors_get_unknown_title_raises():
    api, _ = _actors_api()
    with pytest.raises(ValueError, match="actor 'Nobody' not found"):
        api.get("Nobody")


def test_actors_get_returns_first_of_several_sharing_a_title():
    # Titles are not unique on a live box; get() resolves to the first, matching
    # how the export wizard picks. find_by_title() exposes the ambiguity.
    api, _ = _actors_api()
    assert api.get("Admin").uuid == "aaa"


def test_actors_find_by_title_surfaces_every_duplicate():
    api, _ = _actors_api()
    assert [a.uuid for a in api.find_by_title("Admin")] == ["aaa", "bbb"]


def test_actors_find_by_title_returns_empty_rather_than_raising():
    api, _ = _actors_api()
    assert api.find_by_title("Nobody") == []


def test_actors_get_uses_no_server_side_title_filter():
    # The aggregate ignores a title param — sending one would imply it filters.
    api, c = _actors_api()
    api.get("Admin")
    assert c.calls == [("GET", "/api/3/actors", None)]


def test_unknown_at_type_falls_back_to_user_and_keeps_extra_keys():
    api, _ = _actors_api([{"@id": "/api/3/x/e", "@type": "Martian", "title": "Zork", "uuid": "e", "odd": 1}])
    actor = api.get("Zork")
    assert isinstance(actor, User)
    assert actor.to_dict()["odd"] == 1


# --------------------------------------------------------------------- reporting

_REPORTS = [
    {
        "@id": "/api/3/reporting/r1",
        "@type": "Reporting",
        "uuid": "r1",
        "displayName": "Weekly Alert Report",
        "templateType": "system",
        "type": "report",
        "config": {"a": 1},
        "filterArray": [],
    },
]


def _reporting_api(members=_REPORTS):
    c = FakeClient(lambda m, u, **k: {"hydra:member": members})
    return ReportingAPI(c), c


def test_reporting_list_typed():
    api, _ = _reporting_api()
    reports = api.list()
    assert isinstance(reports[0], Report)
    assert reports[0].displayName == "Weekly Alert Report"


def test_reporting_get_filters_on_display_name_server_side():
    api, c = _reporting_api()
    assert api.get("Weekly Alert Report").uuid == "r1"
    assert c.calls[-1] == ("GET", "/api/3/reporting", {"displayName": "Weekly Alert Report"})


def test_reporting_get_unknown_raises():
    api, _ = _reporting_api([])
    with pytest.raises(ValueError, match="report 'Ghost' not found"):
        api.get("Ghost")


# ------------------------------------------------------------------------- rules

_RULES = [{"uuid": "u1", "name": "Notify On X", "entity_type": "alerts", "is_system": True, "is_active": True}]
_CHANNELS = [{"uuid": "c1", "name": "In-App Notifications", "type": "system", "is_active": True}]
_PRE = [
    {
        "@id": "/api/3/preprocessing_rules/p1",
        "@type": "PreprocessingRule",
        "uuid": "p1",
        "name": "Enforce Files",
        "entityType": "indicators",
        "isActive": True,
    }
]


def _rules_api(handler):
    c = FakeClient(handler)
    return RulesAPI(c), c


def _spa_fallthrough(primary_root):
    """Handler where only ``primary_root`` answers; the other raises like the SPA."""

    def handler(method, url, **kw):
        if not url.startswith(primary_root):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        if url.endswith("rules/"):
            return {"hydra:member": _RULES}
        if url.endswith("channel/"):
            return {"hydra:member": _CHANNELS}
        return {}

    return handler


def test_delivery_rules_typed():
    api, _ = _rules_api(_spa_fallthrough("/rule/api/"))
    rules = api.list_delivery_rules()
    assert isinstance(rules[0], DeliveryRule)
    assert rules[0].name == "Notify On X"


def test_rule_engine_uses_primary_root_when_it_answers():
    api, c = _rules_api(_spa_fallthrough("/rule/api/"))
    api.list_delivery_rules()
    assert c.calls[0][1] == "/rule/api/rules/"
    assert api._root == "/rule/api/"


def test_rule_engine_falls_back_to_alt_root():
    # Live .159 answers on the SECOND root — the fallback is load-bearing.
    api, c = _rules_api(_spa_fallthrough("/api/rule/api/"))
    assert api.list_delivery_rules()[0].name == "Notify On X"
    assert [u for _, u, _ in c.calls] == ["/rule/api/rules/", "/api/rule/api/rules/"]
    assert api._root == "/api/rule/api/"


def test_rule_engine_root_is_cached_across_calls():
    api, c = _rules_api(_spa_fallthrough("/api/rule/api/"))
    api.list_delivery_rules()
    api.list_channels()
    # Second call must not re-probe the dead root.
    assert [u for _, u, _ in c.calls] == [
        "/rule/api/rules/",
        "/api/rule/api/rules/",
        "/api/rule/api/channel/",
    ]


def test_rule_engine_reprobes_when_cached_root_dies():
    state = {"root": "/rule/api/"}

    def handler(method, url, **kw):
        if not url.startswith(state["root"]):
            raise ValueError("SPA fallthrough")
        return {"hydra:member": _RULES}

    api, _ = _rules_api(handler)
    api.list_delivery_rules()
    assert api._root == "/rule/api/"
    # The box moves (e.g. upgrade): the cached root must not wedge the API.
    state["root"] = "/api/rule/api/"
    assert api.list_delivery_rules()[0].name == "Notify On X"
    assert api._root == "/api/rule/api/"


def test_rule_engine_unreachable_raises_runtime_error():
    api, _ = _rules_api(lambda *a, **k: (_ for _ in ()).throw(ValueError("SPA")))
    with pytest.raises(RuntimeError, match="rule-engine app not reachable"):
        api.list_delivery_rules()


def test_channels_typed_and_use_singular_path():
    api, c = _rules_api(_spa_fallthrough("/rule/api/"))
    channels = api.list_channels()
    assert isinstance(channels[0], RuleChannel)
    assert c.calls[0][1] == "/rule/api/channel/"


def test_get_channel_by_name():
    api, _ = _rules_api(_spa_fallthrough("/rule/api/"))
    assert api.get_channel("In-App Notifications").uuid == "c1"


def test_get_channel_unknown_raises():
    api, _ = _rules_api(_spa_fallthrough("/rule/api/"))
    with pytest.raises(ValueError, match="rule channel 'Ghost' not found"):
        api.get_channel("Ghost")


def test_get_delivery_rule_unknown_raises():
    api, _ = _rules_api(_spa_fallthrough("/rule/api/"))
    with pytest.raises(ValueError, match="delivery rule 'Ghost' not found"):
        api.get_delivery_rule("Ghost")


def test_preprocessing_rules_are_crudhub_records_with_iri():
    api, _ = _rules_api(lambda m, u, **k: {"hydra:member": _PRE})
    rule = api.list_preprocessing_rules()[0]
    assert isinstance(rule, PreprocessingRule)
    assert rule.iri == "/api/3/preprocessing_rules/p1"


def test_get_preprocessing_rule_filters_server_side():
    api, c = _rules_api(lambda m, u, **k: {"hydra:member": _PRE})
    assert api.get_preprocessing_rule("Enforce Files").uuid == "p1"
    assert c.calls[-1] == ("GET", "/api/3/preprocessing_rules", {"name": "Enforce Files"})


def test_get_preprocessing_rule_unknown_raises():
    api, _ = _rules_api(lambda m, u, **k: {"hydra:member": []})
    with pytest.raises(ValueError, match="preprocessing rule 'Ghost' not found"):
        api.get_preprocessing_rule("Ghost")
