"""Live edge-case matrix for ``workflow_collections.deploy()`` (opt-in: ``pytest -m integration``).

Every test compiles real playbook YAML, deploys it with ``deploy()``, and then
checks three things against the box:

1. the live playbook's steps and routes are exactly the compiled ones,
2. no step or route row the deploy wrote is left unattached (a leftover makes
   the next deploy 409 on its uuid),
3. the playbook *runs* the new flow -- the executed step names are compared,
   because a structurally plausible playbook can still run the old flow.

Each test works in its own throwaway collection (``ZZ pyfsr deploy ...``) and
removes it, its playbooks and every step/route row it compiled, on the way out.
Runtime checks branch on variable values, so an argument edit is proven by which
branch executes rather than by reading the argument back.
"""

from __future__ import annotations

import copy
import os
from typing import Any

import pytest
import yaml

pytestmark = [pytest.mark.integration, pytest.mark.requires_extra("playbooks")]


# ----------------------------------------------------------------- helpers
def _uuid(ref: Any) -> str:
    if isinstance(ref, dict):
        return ref.get("uuid") or str(ref.get("@id", "")).rsplit("/", 1)[-1]
    return str(ref).rsplit("/", 1)[-1]


def _norm(v: Any) -> Any:
    """FortiSOAR stores an empty JSON object as ``[]``; compare them as equal."""
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()} or None
    if isinstance(v, list):
        return [_norm(x) for x in v] or None
    return v


def chain(*names: str, end: bool = True) -> list[dict[str, Any]]:
    """Start -> set_variable steps named ``names`` -> Done, in order."""
    steps: list[dict[str, Any]] = [{"name": "Start", "type": "start"}]
    steps += [{"name": n, "type": "set_variable", "vars": {"marker": n}} for n in names]
    if end:
        steps.append({"name": "Done", "type": "end"})
    for a, b in zip(steps, steps[1:]):
        a["next"] = b["name"]
    return steps


class Box:
    """A throwaway collection on the live box plus the checks every test needs."""

    def __init__(self, client, name: str):
        self.c = client
        self.name = name
        self.envelopes: list[dict[str, Any]] = []  # what the checks consider
        self.compiled: list[dict[str, Any]] = []  # everything ever compiled, for cleanup

    # -- authoring --------------------------------------------------------
    def yaml(self, playbooks: list[dict[str, Any]]) -> str:
        return yaml.safe_dump({"collection": self.name, "visible": True, "playbooks": playbooks}, sort_keys=False)

    def compile(self, playbooks: list[dict[str, Any]]) -> dict[str, Any]:
        res = self.c.workflow_collections.compile_yaml(self.yaml(playbooks), refresh_catalog=False)
        assert res.ok and res.fsr_json, f"compile failed: {[str(e) for e in res.errors]}"
        env = res.fsr_json
        self.envelopes.append(copy.deepcopy(env))
        self.compiled.append(copy.deepcopy(env))
        return env

    def deploy(self, playbooks: list[dict[str, Any]], **kw) -> tuple[dict[str, Any], dict[str, Any]]:
        env = self.compile(playbooks)
        report = self.c.workflow_collections.deploy(copy.deepcopy(env), **kw)
        return env, report

    # -- reading the box --------------------------------------------------
    def live(self, wf_uuid: str) -> dict[str, Any] | None:
        try:
            wf = self.c.get(f"/api/3/workflows/{wf_uuid}", params={"$relationships": "true"})
        except Exception:
            return None
        return wf if isinstance(wf, dict) and wf.get("uuid") else None

    def exists(self, entity: str, uuid: str) -> bool:
        try:
            return bool(self.c.get(f"/api/3/{entity}/{uuid}"))
        except Exception:
            return False

    def assert_matches(self, env: dict[str, Any]) -> None:
        """Live steps/routes == compiled ones, and nothing the deploy wrote is stranded."""
        attached = {"workflow_steps": set(), "workflow_routes": set()}
        for wf in env["data"][0]["workflows"]:
            live = self.live(wf["uuid"])
            assert live, f"playbook {wf['name']!r} is not on the box"
            for key, entity in (("steps", "workflow_steps"), ("routes", "workflow_routes")):
                want = {r["uuid"] for r in wf.get(key) or []}
                got = {_uuid(r) for r in live.get(key) or []}
                attached[entity] |= got
                assert got == want, f"{wf['name']}: {key} missing {want - got}, unexpected {got - want}"
            by_uuid = {_uuid(s): s for s in live.get("steps") or []}
            for s in wf.get("steps") or []:
                ls = by_uuid[s["uuid"]]
                assert ls.get("name") == s["name"]
                assert _uuid(ls.get("stepType")) == _uuid(s.get("stepType")), f"{s['name']}: stepType not updated"
                assert _norm(ls.get("arguments")) == _norm(s.get("arguments")), f"{s['name']}: arguments not updated"
        self.assert_no_stranded_rows(attached)

    def assert_no_stranded_rows(self, attached: dict[str, set[str]] | None = None) -> None:
        if attached is None:
            attached = {"workflow_steps": set(), "workflow_routes": set()}
            for wf in self.envelopes[-1]["data"][0]["workflows"]:
                live = self.live(wf["uuid"]) or {}
                attached["workflow_steps"] |= {_uuid(s) for s in live.get("steps") or []}
                attached["workflow_routes"] |= {_uuid(r) for r in live.get("routes") or []}
        stranded = [
            f"{entity}/{u}"
            for entity, key in (("workflow_steps", "steps"), ("workflow_routes", "routes"))
            for u in self._compiled_uuids(key)
            if u not in attached[entity] and self.exists(entity, u)
        ]
        assert not stranded, f"rows left unattached (the next deploy would 409 on them): {stranded}"

    def _compiled_uuids(self, key: str, envelopes: list[dict[str, Any]] | None = None) -> set[str]:
        envs = self.envelopes if envelopes is None else envelopes
        return {r["uuid"] for env in envs for wf in env["data"][0]["workflows"] for r in wf.get(key) or []}

    def run(self, playbook: str) -> set[str]:
        res = self.c.playbooks.run_and_wait(playbook, timeout=180)
        assert res.status == "finished", f"{playbook} run {res.status}: {res.failure}"
        return {s.name for s in res.steps if s.status != "skipped"}

    # -- cleanup ------------------------------------------------------------
    def purge(self) -> None:
        wf_uuids = {wf["uuid"] for env in self.compiled for wf in env["data"][0]["workflows"]}
        coll_uuids = {env["data"][0]["uuid"] for env in self.compiled}
        try:
            coll = self.c.workflow_collections._resolve_collection(self.name)
            coll_uuids.add(coll.uuid if hasattr(coll, "uuid") else coll["uuid"])
            members = (coll.to_dict() if hasattr(coll, "to_dict") else coll).get("workflows") or []
            wf_uuids |= {w["uuid"] for w in members}
        except Exception:
            pass
        for u in wf_uuids:
            self._hard_delete("workflows", u)
        for key, entity in (("steps", "workflow_steps"), ("routes", "workflow_routes")):
            for u in self._compiled_uuids(key, self.compiled):
                self._hard_delete(entity, u)
        for u in coll_uuids:
            self._hard_delete("workflow_collections", u)

    def _hard_delete(self, entity: str, uuid: str) -> None:
        try:
            # $showDeleted reaches rows already in the recycle bin; without it the
            # hard delete 404s on them and they stay, holding their uuids.
            self.c.request("DELETE", f"/api/3/{entity}/{uuid}", params={"$hardDelete": "true", "$showDeleted": "true"})
        except Exception:
            pass


@pytest.fixture
def box(client, request):
    b = Box(client, f"ZZ pyfsr deploy {request.node.name[5:40]} {os.getpid()}")
    b.purge()
    yield b
    b.purge()


def pb(name: str, steps: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"name": name, "is_active": True, **extra, "steps": steps}


# ------------------------------------------------------------- the matrix
def test_fresh_deploy_creates_and_runs(box):
    env, report = box.deploy([pb("ZZ Flow", chain("A", "B"))])
    assert report["created"] and not report["updated"]
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "B", "Done"}


def test_identical_redeploy_is_a_clean_noop(box):
    box.deploy([pb("ZZ Flow", chain("A", "B"))])
    env, report = box.deploy([pb("ZZ Flow", chain("A", "B"))])
    box.assert_matches(env)
    assert report["changed"] == [], f"identical redeploy reported as changed: {report['changed']}"
    assert box.run("ZZ Flow") == {"Start", "A", "B", "Done"}


def test_add_step_mid_chain(box):
    box.deploy([pb("ZZ Flow", chain("A", "B"))])
    env, _ = box.deploy([pb("ZZ Flow", chain("A", "X", "B"))])
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "X", "B", "Done"}


def test_remove_step(box):
    box.deploy([pb("ZZ Flow", chain("A", "X", "B"))])
    env, _ = box.deploy([pb("ZZ Flow", chain("A", "B"))], overwrite_changed=True)
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "B", "Done"}


def test_rename_step(box):
    box.deploy([pb("ZZ Flow", chain("A", "B"))])
    env, _ = box.deploy([pb("ZZ Flow", chain("A", "B Renamed"))], overwrite_changed=True)
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "B Renamed", "Done"}


def test_reorder_steps(box):
    box.deploy([pb("ZZ Flow", chain("A", "B", "C"))])
    env, _ = box.deploy([pb("ZZ Flow", chain("C", "A", "B"))])
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "B", "C", "Done"}


def test_change_step_type_keeping_name(box):
    box.deploy([pb("ZZ Flow", chain("A", "B"))])
    steps = chain("A", "B")
    steps[2] = {"name": "B", "type": "delay", "seconds": 1, "next": "Done"}
    env, _ = box.deploy([pb("ZZ Flow", steps)])
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "B", "Done"}


def _branching(value: str) -> list[dict[str, Any]]:
    """Set ``choice`` then branch on it -- the branch that runs proves the value."""
    return [
        {"name": "Start", "type": "start", "next": "Pick"},
        {"name": "Pick", "type": "set_variable", "vars": {"choice": value}, "next": "Route"},
        {
            "name": "Route",
            "type": "decision",
            "conditions": [
                {"display": "left", "when": "{{ vars.choice == 'left' }}", "next": "Went Left"},
                {"display": "Else", "default": True, "next": "Went Right"},
            ],
        },
        {"name": "Went Left", "type": "set_variable", "vars": {"went": "left"}, "next": "Done"},
        {"name": "Went Right", "type": "set_variable", "vars": {"went": "right"}, "next": "Done"},
        {"name": "Done", "type": "end"},
    ]


def test_step_argument_edit_changes_runtime(box):
    box.deploy([pb("ZZ Branch", _branching("left"))])
    assert "Went Left" in box.run("ZZ Branch")
    env, _ = box.deploy([pb("ZZ Branch", _branching("right"))])
    box.assert_matches(env)
    ran = box.run("ZZ Branch")
    assert "Went Right" in ran and "Went Left" not in ran


def test_decision_condition_edit_changes_runtime(box):
    box.deploy([pb("ZZ Branch", _branching("left"))])
    steps = _branching("left")
    steps[2]["conditions"][0]["when"] = "{{ vars.choice == 'never' }}"
    env, _ = box.deploy([pb("ZZ Branch", steps)])
    box.assert_matches(env)
    assert "Went Right" in box.run("ZZ Branch")


def test_add_decision_branch(box):
    box.deploy([pb("ZZ Branch", _branching("middle"))])
    steps = _branching("middle")
    steps[2]["conditions"].insert(
        1, {"display": "middle", "when": "{{ vars.choice == 'middle' }}", "next": "Went Middle"}
    )
    steps.insert(5, {"name": "Went Middle", "type": "set_variable", "vars": {"went": "middle"}, "next": "Done"})
    env, _ = box.deploy([pb("ZZ Branch", steps)])
    box.assert_matches(env)
    assert "Went Middle" in box.run("ZZ Branch")


def test_trigger_change_to_module_button_and_relabel(box):
    box.deploy([pb("ZZ Button", chain("A"))])
    steps = chain("A")
    steps[0].update({"module": "alerts", "button_label": "ZZ Button One"})
    env, _ = box.deploy([pb("ZZ Button", steps)])
    box.assert_matches(env)
    labels = {p.get("label") or p.get("button_label") for p in box.c.playbooks.manual_on_module("alerts")}
    assert "ZZ Button One" in labels
    steps[0]["button_label"] = "ZZ Button Two"
    env, _ = box.deploy([pb("ZZ Button", steps)])
    box.assert_matches(env)
    labels = {p.get("label") or p.get("button_label") for p in box.c.playbooks.manual_on_module("alerts")}
    assert "ZZ Button Two" in labels and "ZZ Button One" not in labels


def test_fresh_deploy_with_module_button(box):
    steps = chain("A")
    steps[0].update({"module": "alerts", "button_label": "ZZ Fresh Button"})
    env, report = box.deploy([pb("ZZ Button", steps)])
    assert report["created"]
    box.assert_matches(env)
    labels = {p.get("label") or p.get("button_label") for p in box.c.playbooks.manual_on_module("alerts")}
    assert "ZZ Fresh Button" in labels


def test_activate_inactive_playbook(box):
    box.deploy([pb("ZZ Flow", chain("A"), is_active=False)])
    wf_uuid = box.envelopes[-1]["data"][0]["workflows"][0]["uuid"]
    assert box.live(wf_uuid)["isActive"] is False
    env, _ = box.deploy([pb("ZZ Flow", chain("A"), is_active=True)])
    assert box.live(wf_uuid)["isActive"] is True
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "Done"}


def test_add_playbook_to_existing_collection_leaves_sibling_alone(box):
    box.deploy([pb("ZZ One", chain("A"))])
    env, report = box.deploy([pb("ZZ One", chain("A")), pb("ZZ Two", chain("B"))])
    assert report["new"] == ["ZZ Two"]
    box.assert_matches(env)
    assert box.run("ZZ One") == {"Start", "A", "Done"}
    assert box.run("ZZ Two") == {"Start", "B", "Done"}


def test_parent_gains_reference_to_new_child_in_same_deploy(box):
    box.deploy([pb("ZZ Parent", chain("A"))])
    parent = chain("A")
    parent.insert(2, {"name": "Call Child", "type": "workflow_reference", "target": "ZZ Child", "next": "Done"})
    parent[1]["next"] = "Call Child"
    env, _ = box.deploy([pb("ZZ Parent", parent), pb("ZZ Child", chain("In Child"))])
    box.assert_matches(env)
    assert "Call Child" in box.run("ZZ Parent")


def test_unlisted_playbook_survives_without_prune(box):
    box.deploy([pb("ZZ Keep", chain("A")), pb("ZZ Other", chain("B"))])
    env, report = box.deploy([pb("ZZ Keep", chain("A"))])
    assert "ZZ Other" in report["orphans"] and not report["pruned"]
    assert box.run("ZZ Other") == {"Start", "B", "Done"}


def test_prune_then_readd_same_playbook(box):
    """A pruned playbook sits in the recycle bin under its uuid; re-adding it must not 409."""
    box.deploy([pb("ZZ Keep", chain("A")), pb("ZZ Back", chain("B"))])
    _, report = box.deploy([pb("ZZ Keep", chain("A"))], prune=True)
    assert report["pruned"]
    env, _ = box.deploy([pb("ZZ Keep", chain("A")), pb("ZZ Back", chain("B"))])
    box.assert_matches(env)
    assert box.run("ZZ Back") == {"Start", "B", "Done"}


def test_existing_collection_with_same_name_different_uuid(box):
    """A collection made by another tool: deploy must land in it by name."""
    made = box.c.workflow_collections.create_collection(box.name)
    made_uuid = made.uuid if hasattr(made, "uuid") else made["uuid"]
    env, report = box.deploy([pb("ZZ Flow", chain("A"))])
    assert report["collection_uuid"] == made_uuid
    wf = box.live(env["data"][0]["workflows"][0]["uuid"])
    assert _uuid(wf["collection"]) == made_uuid
    assert box.run("ZZ Flow") == {"Start", "A", "Done"}


def test_collection_in_recycle_bin(box):
    box.deploy([pb("ZZ Flow", chain("A"))])
    coll_uuid = box.envelopes[-1]["data"][0]["uuid"]
    for wf in box.envelopes[-1]["data"][0]["workflows"]:
        box.c.playbooks.delete(wf["uuid"], hard=False)
    box.c.request("DELETE", f"/api/3/workflow_collections/{coll_uuid}")
    env, _ = box.deploy([pb("ZZ Flow", chain("A"))])
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "Done"}


def test_playbook_in_recycle_bin(box):
    box.deploy([pb("ZZ Flow", chain("A"))])
    wf_uuid = box.envelopes[-1]["data"][0]["workflows"][0]["uuid"]
    box.c.playbooks.delete(wf_uuid, hard=False)
    env, _ = box.deploy([pb("ZZ Flow", chain("A", "B"))])
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "B", "Done"}


def test_steps_stranded_by_collection_hard_delete(box):
    """Hard-deleting a collection while its playbook is in the recycle bin purges
    the playbook but leaves its step/route rows behind, attached to nothing and
    invisible to every playbook lookup. A redeploy must adopt them, not 409."""
    env, _ = box.deploy([pb("ZZ Flow", chain("A"))])
    box.c.playbooks.delete(env["data"][0]["workflows"][0]["uuid"], hard=False)
    box.c.request("DELETE", f"/api/3/workflow_collections/{env['data'][0]['uuid']}", params={"$hardDelete": "true"})
    env, report = box.deploy([pb("ZZ Flow", chain("A", "B"))])
    assert report["created"]
    # The purged playbook's own A->Done route stays behind; it is not part of
    # this version, and a later version that needs it adopts it -- so only this
    # version's rows must be attached.
    box.envelopes = box.envelopes[-1:]
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "B", "Done"}


def test_leftover_unattached_step_and_route_are_adopted(box):
    box.deploy([pb("ZZ Flow", chain("A", "B"))])
    target = box.compile([pb("ZZ Flow", chain("A", "X", "B"))])
    wf = target["data"][0]["workflows"][0]
    strip = ("@context", "@id", "@type", "id", "createDate", "modifyDate")
    x = next(s for s in wf["steps"] if s["name"] == "X")
    box.c.post("/api/3/workflow_steps", data={k: v for k, v in x.items() if k not in strip})
    r = next(r for r in wf["routes"] if _uuid(r["targetStep"]) == x["uuid"])
    try:
        box.c.post("/api/3/workflow_routes", data={k: v for k, v in r.items() if k not in strip})
    except Exception:
        pass  # a route needs its workflow on some builds; the step leftover is the main case
    env, _ = box.deploy([pb("ZZ Flow", chain("A", "X", "B"))])
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "X", "B", "Done"}


def test_step_in_recycle_bin_is_readded(box):
    box.deploy([pb("ZZ Flow", chain("A", "X", "B"))])
    x_uuid = next(s["uuid"] for s in box.envelopes[-1]["data"][0]["workflows"][0]["steps"] if s["name"] == "X")
    box.deploy([pb("ZZ Flow", chain("A", "B"))], overwrite_changed=True)
    if box.exists("workflow_steps", x_uuid):
        box.c.request("DELETE", f"/api/3/workflow_steps/{x_uuid}")  # soft delete -> recycle bin
    env, _ = box.deploy([pb("ZZ Flow", chain("A", "X", "B"))])
    box.assert_matches(env)
    assert box.run("ZZ Flow") == {"Start", "A", "X", "B", "Done"}


def test_box_only_step_blocks_deploy_and_changes_nothing(box):
    env1, _ = box.deploy([pb("ZZ Flow", chain("A", "X", "B"))])
    with pytest.raises(ValueError, match="box-only steps"):
        box.deploy([pb("ZZ Flow", chain("A", "B"))])
    box.envelopes.pop()
    box.assert_matches(env1)
    assert box.run("ZZ Flow") == {"Start", "A", "X", "B", "Done"}


def test_dry_run_changes_nothing(box):
    env1, _ = box.deploy([pb("ZZ Flow", chain("A", "B"))])
    _, report = box.deploy([pb("ZZ Flow", chain("A", "X", "B"))], dry_run=True)
    assert report["changed"] == ["ZZ Flow"]
    box.envelopes.pop()
    box.assert_matches(env1)


def test_backup_restores_previous_version_through_deploy(box, tmp_path):
    env1, _ = box.deploy([pb("ZZ Flow", chain("A", "B"))])
    _, report = box.deploy([pb("ZZ Flow", chain("A", "X", "B"))], backup_dir=tmp_path)
    import json

    backup = json.loads(open(report["backup"]).read())
    box.c.workflow_collections.deploy(backup, overwrite_changed=True)
    box.assert_matches(env1)
    assert box.run("ZZ Flow") == {"Start", "A", "B", "Done"}


def test_failed_update_leaves_previous_version_runnable(box):
    """A deploy that the server rejects must not leave a half-applied playbook."""
    env1, _ = box.deploy([pb("ZZ Flow", chain("A", "B"))])
    bad = box.compile([pb("ZZ Flow", chain("A", "X", "B"))])
    for s in bad["data"][0]["workflows"][0]["steps"]:
        if s["name"] == "X":
            s["stepType"] = "/api/3/workflow_step_types/00000000-0000-0000-0000-000000000000"
    with pytest.raises(Exception):
        box.c.workflow_collections.deploy(copy.deepcopy(bad))
    box.envelopes.pop()
    box.assert_matches(env1)
    assert box.run("ZZ Flow") == {"Start", "A", "B", "Done"}
