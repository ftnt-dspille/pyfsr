"""In-place playbook updates via PlaybooksAPI.upsert_playbooks.

The fake server below models the behaviour live-verified on 8.0.0:

- ``PUT /api/3/workflows/<uuid>`` with ``steps``/``routes`` replaces the
  playbook's membership: IRIs keep existing rows, dicts create new attached
  rows, and rows left out are deleted.
- ``POST /api/3/workflow_steps`` creates a step but ignores its ``workflow``
  field, so the step is left unattached (the original bug).
- A uuid that already exists anywhere conflicts on create.
- A soft-deleted playbook 404s on a plain GET, shows up with ``$showDeleted``,
  and comes back when its ``deletedAt`` is cleared.
"""

import pytest

from pyfsr.api.playbooks import PlaybooksAPI
from pyfsr.exceptions import APIError

WF = "11111111-1111-1111-1111-111111111111"
WF_IRI = f"/api/3/workflows/{WF}"


def _uuid(ref):
    return ref["uuid"] if isinstance(ref, dict) else ref.rsplit("/", 1)[-1]


class FakeServer:
    def __init__(self, steps, routes, *, drop_relations=False, wf_state="live"):
        self.wf_state = wf_state  # "live", "deleted" (recycle bin) or None (absent)
        self.rows = {"workflow_steps": {}, "workflow_routes": {}}
        self.attached = {"steps": [], "routes": []}
        for key, entity, items in (("steps", "workflow_steps", steps), ("routes", "workflow_routes", routes)):
            for item in items:
                self.rows[entity][item["uuid"]] = dict(item)
                self.attached[key].append(item["uuid"])
        self.calls = []
        self.drop_relations = drop_relations  # simulate a server that ignores the relation PUT

    def get(self, endpoint, params=None, **kw):
        self.calls.append(("GET", endpoint))
        if endpoint.startswith(WF_IRI):
            show_deleted = (params or {}).get("$showDeleted") == "true"
            if self.wf_state is None or (self.wf_state == "deleted" and not show_deleted):
                raise RuntimeError("404 Not Found")
            if self.wf_state == "deleted":
                return {"uuid": WF, "deletedAt": 1790000000.0}
            return {
                "uuid": WF,
                "steps": [{"@id": f"/api/3/workflow_steps/{u}", "uuid": u} for u in self.attached["steps"]],
                "routes": [{"@id": f"/api/3/workflow_routes/{u}", "uuid": u} for u in self.attached["routes"]],
            }
        for entity, rows in self.rows.items():
            prefix = f"/api/3/{entity}/"
            if endpoint.startswith(prefix):
                u = endpoint[len(prefix) :]
                if u in rows:
                    return rows[u]
                raise RuntimeError("404 Not Found")
        raise AssertionError(f"unexpected GET {endpoint}")

    def put(self, endpoint, data=None, **kw):
        self.calls.append(("PUT", endpoint, data))
        if endpoint == WF_IRI and self.wf_state == "deleted":
            assert (kw.get("params") or {}).get("$showDeleted") == "true", "PUT on a deleted row 404s"
            if "deletedAt" in (data or {}) and data["deletedAt"] is None:
                self.wf_state = "live"
            return {}
        if endpoint == WF_IRI:
            if self.drop_relations:
                return {}
            for key, entity in (("steps", "workflow_steps"), ("routes", "workflow_routes")):
                if key not in (data or {}):
                    continue
                keep = []
                for ref in data[key]:
                    u = _uuid(ref)
                    if isinstance(ref, dict):
                        assert u not in self.rows[entity], f"409: {u} already exists"
                        self.rows[entity][u] = dict(ref)
                    keep.append(u)
                for u in set(self.attached[key]) - set(keep):
                    self.rows[entity].pop(u, None)
                self.attached[key] = keep
            return {}
        for entity, rows in self.rows.items():
            prefix = f"/api/3/{entity}/"
            if endpoint.startswith(prefix):
                rows[endpoint[len(prefix) :]].update(data or {})
                return {}
        raise AssertionError(f"unexpected PUT {endpoint}")

    def post(self, endpoint, data=None, **kw):
        self.calls.append(("POST", endpoint, data))
        if endpoint == "/api/3/workflows":
            assert self.wf_state is None, "409: workflow uuid already exists"
            assert not data.get("steps") and not data.get("routes"), "shell create must carry no children"
            self.wf_state = "live"
            return {"@id": WF_IRI, "uuid": data["uuid"]}
        if endpoint == "/api/3/workflow_steps":
            u = data["uuid"]
            assert u not in self.rows["workflow_steps"], f"409: {u} already exists"
            self.rows["workflow_steps"][u] = dict(data)  # created, but NOT attached
            return {"@id": f"/api/3/workflow_steps/{u}"}
        raise AssertionError(f"unexpected POST {endpoint}")

    def delete(self, endpoint, **kw):
        self.calls.append(("DELETE", endpoint))


def _step(u, name):
    return {"uuid": u, "name": name, "arguments": {}}


def _route(u, src, dst):
    return {"uuid": u, "sourceStep": f"/api/3/workflow_steps/{src}", "targetStep": f"/api/3/workflow_steps/{dst}"}


V1_STEPS = [_step("s-start", "Start"), _step("s-a", "A"), _step("s-b", "B")]
V1_ROUTES = [_route("r-start-a", "s-start", "s-a"), _route("r-a-b", "s-a", "s-b")]
V2_STEPS = [_step("s-start", "Start"), _step("s-a", "A"), _step("s-x", "X"), _step("s-b", "B")]
V2_ROUTES = [_route("r-start-a", "s-start", "s-a"), _route("r-a-x", "s-a", "s-x"), _route("r-x-b", "s-x", "s-b")]


def _row(steps, routes):
    return {"uuid": WF, "name": "PB", "steps": steps, "routes": routes}


def test_adding_a_step_attaches_it_and_rewires_routes():
    srv = FakeServer(V1_STEPS, V1_ROUTES)
    res = PlaybooksAPI(srv).upsert_playbooks([_row(V2_STEPS, V2_ROUTES)])

    assert res == {"created": [], "updated": [WF]}
    assert srv.attached["steps"] == ["s-start", "s-a", "s-x", "s-b"]
    assert srv.attached["routes"] == ["r-start-a", "r-a-x", "r-x-b"]
    assert "r-a-b" not in srv.rows["workflow_routes"]  # the replaced route is gone


def test_new_steps_are_never_posted_standalone():
    """A bare POST leaves the step unattached on a real box -- the original bug."""
    srv = FakeServer(V1_STEPS, V1_ROUTES)
    PlaybooksAPI(srv).upsert_playbooks([_row(V2_STEPS, V2_ROUTES)])
    assert not [c for c in srv.calls if c[0] == "POST"]


def test_removing_a_step_drops_it_and_its_routes():
    srv = FakeServer(V2_STEPS, V2_ROUTES)
    PlaybooksAPI(srv).upsert_playbooks([_row(V1_STEPS, V1_ROUTES)])

    assert srv.attached["steps"] == ["s-start", "s-a", "s-b"]
    assert srv.attached["routes"] == ["r-start-a", "r-a-b"]
    assert "s-x" not in srv.rows["workflow_steps"]


def test_existing_step_edits_land():
    srv = FakeServer(V1_STEPS, V1_ROUTES)
    edited = [dict(s) for s in V1_STEPS]
    edited[1] = {**edited[1], "arguments": {"a": 42}}
    PlaybooksAPI(srv).upsert_playbooks([_row(edited, V1_ROUTES)])
    assert srv.rows["workflow_steps"]["s-a"]["arguments"] == {"a": 42}


def test_unattached_leftover_is_adopted_not_recreated():
    """A step left unattached by an earlier interrupted update must not 409."""
    srv = FakeServer(V1_STEPS, V1_ROUTES)
    srv.rows["workflow_steps"]["s-x"] = _step("s-x", "X")  # leftover, not attached

    PlaybooksAPI(srv).upsert_playbooks([_row(V2_STEPS, V2_ROUTES)])

    assert "s-x" in srv.attached["steps"]
    sent = next(c[2] for c in srv.calls if c[0] == "PUT" and c[1] == WF_IRI and "steps" in (c[2] or {}))
    assert "/api/3/workflow_steps/s-x" in sent["steps"]  # referenced by IRI, not re-created


def test_row_without_routes_leaves_routes_alone():
    srv = FakeServer(V1_STEPS, V1_ROUTES)
    PlaybooksAPI(srv).upsert_playbooks([{"uuid": WF, "name": "PB", "steps": V1_STEPS}])
    sent = next(c[2] for c in srv.calls if c[0] == "PUT" and c[1] == WF_IRI and "steps" in (c[2] or {}))
    assert "routes" not in sent
    assert srv.attached["routes"] == ["r-start-a", "r-a-b"]


def test_update_that_does_not_take_effect_raises():
    """Never report 'updated' when the live playbook still runs the old flow."""
    srv = FakeServer(V1_STEPS, V1_ROUTES, drop_relations=True)
    with pytest.raises(APIError, match="did not take effect"):
        PlaybooksAPI(srv).upsert_playbooks([_row(V2_STEPS, V2_ROUTES)])


def test_new_playbook_is_created_as_a_shell_then_filled():
    """No bulkupsert: a shell POST, then the same in-place fill as an update."""
    srv = FakeServer([], [], wf_state=None)
    res = PlaybooksAPI(srv).upsert_playbooks([_row(V2_STEPS, V2_ROUTES)])

    assert res == {"created": [WF], "updated": []}
    assert srv.attached["steps"] == ["s-start", "s-a", "s-x", "s-b"]
    assert not [c for c in srv.calls if c[0] == "POST" and "bulkupsert" in c[1]]


def test_new_playbook_adopts_step_rows_its_previous_life_left_behind():
    """Hard-deleting a collection purges its recycle-bin playbooks but strands
    their steps; a create must reuse them rather than 409 on their uuids."""
    srv = FakeServer([], [], wf_state=None)
    for s in V1_STEPS:
        srv.rows["workflow_steps"][s["uuid"]] = dict(s)  # stranded, attached to nothing

    PlaybooksAPI(srv).upsert_playbooks([_row(V2_STEPS, V2_ROUTES)])
    assert srv.attached["steps"] == ["s-start", "s-a", "s-x", "s-b"]


def test_playbook_in_recycle_bin_is_restored_and_updated():
    srv = FakeServer(V1_STEPS, V1_ROUTES, wf_state="deleted")
    res = PlaybooksAPI(srv).upsert_playbooks([_row(V2_STEPS, V2_ROUTES)])

    assert srv.wf_state == "live"
    assert res == {"created": [], "updated": [WF]}
    assert srv.attached["steps"] == ["s-start", "s-a", "s-x", "s-b"]
    assert not [c for c in srv.calls if c[0] == "POST"]
