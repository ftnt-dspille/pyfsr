"""Unit tests for the picklist write helpers (create / get_or_create / add /
remove) on PicklistsAPI.

These mock the client so no appliance is needed; the live 8.0 wire shapes they
assert against were captured from a live appliance and live-verified end-to-end
(see the picklist-write memory).
"""

from __future__ import annotations

from pyfsr.api.picklists import PicklistsAPI
from pyfsr.exceptions import ResourceNotFoundError


class _MockResp:
    def __init__(self, status, body=None):
        self.status_code = status
        self.text = ""
        self._body = body or {}

    def json(self):
        return self._body


class WriteClient:
    """A fake client that simulates the picklist write endpoints with state."""

    def __init__(self):
        # name -> {list dict}; iri -> {item dict}
        self.lists: dict[str, dict] = {}
        self.items: dict[str, dict] = {}
        self._seq = 100
        self.deleted_lists: list[str] = []
        self.deleted_items: list[str] = []

    def _uuid(self):
        self._seq += 1
        return f"00000000-0000-0000-0000-{self._seq:012d}"

    def get(self, endpoint, params=None, **kwargs):
        # single list by IRI
        if endpoint.startswith("/api/3/picklist_names/"):
            iri = endpoint
            lst = self.lists.get(iri)
            if lst is None:
                raise ResourceNotFoundError("not found")
            return self._list_with_items(lst)
        # collection of lists
        if endpoint == "/api/3/picklist_names":
            return {"hydra:member": list(self.lists.values())}
        if endpoint == "/api/3/picklists":
            return {"hydra:member": list(self.items.values())}
        return {"hydra:member": []}

    def _list_with_items(self, lst):
        out = dict(lst)
        out["picklists"] = [it for it in self.items.values() if it.get("listName") == lst["@id"]]
        return out

    def post(self, endpoint, data=None, params=None, **kwargs):
        data = data or {}
        if endpoint == "/api/3/picklist_names":
            name = data.get("name")
            if name and any(lst.get("name") == name for lst in self.lists.values()):
                return _MockResp(409, {"type": "UniqueConstraintViolationException", "message": "dup"})
            iri = f"/api/3/picklist_names/{self._uuid()}"
            lst = {
                "@id": iri,
                "@type": "PicklistName",
                "name": name,
                "system": data.get("system", False),
                "uuid": iri.rsplit("/", 1)[-1],
                "picklists": [],
            }
            self.lists[iri] = lst
            return lst
        if endpoint == "/api/3/picklists":
            iri = f"/api/3/picklists/{self._uuid()}"
            item = {
                "@id": iri,
                "@type": "Picklist",
                "itemValue": data.get("itemValue"),
                "listName": data.get("listName"),
                "orderIndex": data.get("orderIndex"),
                "color": data.get("color"),
                "icon": None,
                "uuid": iri.rsplit("/", 1)[-1],
            }
            self.items[iri] = item
            return item
        return {}

    def delete(self, endpoint, params=None, **kwargs):
        if endpoint.startswith("/api/3/picklist_names/"):
            self.lists.pop(endpoint, None)
            self.deleted_lists.append(endpoint)
            # cascade: drop items pointing at this list
            self.items = {k: v for k, v in self.items.items() if v.get("listName") != endpoint}
        elif endpoint.startswith("/api/3/picklists/"):
            self.items.pop(endpoint, None)
            self.deleted_items.append(endpoint)
        return None


def _api():
    client = WriteClient()
    api = PicklistsAPI(client)
    return api, client


def test_create_picklist_returns_list_with_options():
    api, client = _api()
    pn = api.create_picklist("ReconStatus", options=["Open", "Closed"])
    assert pn.name == "ReconStatus"
    assert pn.iri and pn.iri.startswith("/api/3/picklist_names/")
    assert {i.itemValue for i in pn.items} == {"Open", "Closed"}
    # options were POSTed with sequential orderIndex
    orders = sorted(it.order_index for it in pn.items)
    assert orders == [0, 1]


def test_create_picklist_with_option_dicts():
    api, _ = _api()
    pn = api.create_picklist(
        "M", options=[{"value": "Low", "color": "#00FF00"}, {"value": "High", "color": "#FF0000", "order": 9}]
    )
    high = next(i for i in pn.items if i.itemValue == "High")
    assert high.color == "#FF0000" and high.order_index == 9
    low = next(i for i in pn.items if i.itemValue == "Low")
    assert low.color == "#00FF00"


def test_get_or_create_picklist_is_idempotent():
    api, _ = _api()
    pn1, created1 = api.get_or_create_picklist("MismatchType", options=["A", "B"])
    assert created1 is True
    assert {i.itemValue for i in pn1.items} == {"A", "B"}
    # second call: exists, NOT recreated, options NOT clobbered
    pn2, created2 = api.get_or_create_picklist("MismatchType", options=["SHOULD-NOT-APPEAR"])
    assert created2 is False
    assert {i.itemValue for i in pn2.items} == {"A", "B"}


def test_add_option_returns_item_with_iri():
    api, _ = _api()
    api.create_picklist("Status")
    item = api.add_option("Status", "Open", color="#000000", order=5)
    assert item.itemValue == "Open"
    assert item.color == "#000000"
    assert item.order_index == 5
    assert item.iri and item.iri.startswith("/api/3/picklists/")


def test_add_option_accepts_iri_or_uuid():
    api, _ = _api()
    pn = api.create_picklist("Status")
    item = api.add_option(pn.iri, "Open")
    assert item.itemValue == "Open"
    item2 = api.add_option(pn.uuid, "Closed")
    assert item2.itemValue == "Closed"


def test_add_option_unknown_picklist_raises():
    api, _ = _api()
    try:
        api.add_option("Nope", "X")
    except ValueError as e:
        assert "Nope" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown picklist")


def test_remove_option_by_value():
    api, client = _api()
    api.create_picklist("Status", options=["Open", "Closed"])
    assert api.remove_option("Status", value="Open") is True
    assert "Open" not in api.options("Status")
    # missing_ok default -> False, no raise
    assert api.remove_option("Status", value="Open") is False


def test_remove_option_by_item_iri():
    api, client = _api()
    api.create_picklist("Status", options=["Open"])
    item_iri = api.resolve("Open", picklist="Status")
    assert api.remove_option(item=item_iri) is True
    assert "Open" not in api.options("Status")


def test_remove_option_requires_value_or_item():
    api, _ = _api()
    try:
        api.remove_option("Status")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when neither value nor item given")


def test_remove_picklist_cascades_items():
    api, client = _api()
    api.create_picklist("Status", options=["Open", "Closed"])
    assert api.remove_picklist("Status") is True
    assert "Status" not in api.list()
    # cascade: the items under it are gone from the store
    assert not [it for it in client.items.values() if it.get("listName", "").endswith("Status")]


def test_remove_picklist_missing_ok():
    api, _ = _api()
    assert api.remove_picklist("Ghost") is False


def test_get_picklist_by_name_returns_none_when_absent():
    api, _ = _api()
    assert api.get_picklist("Ghost") is None
