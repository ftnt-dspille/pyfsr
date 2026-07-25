"""Unit tests for PlaybooksAPI.run_tree() and step_status()."""

import re

import pytest

from pyfsr.api.playbooks import PlaybooksAPI


class TreeClient:
    """Fake client modelling a run table with parent_wf links + step detail.

    ``runs`` maps pk -> {"name", "status", "parent": <pk|None>, "steps": {name: status}}.
    """

    def __init__(self, runs, task_map=None):
        self.runs = runs
        self.task_map = task_map or {}  # task_id uuid -> pk

    def get(self, endpoint, params=None, **kw):
        # single-run fetch: /api/wf/api/workflows/<pk>/?format=json[&step_detail=true]
        m = re.search(r"/workflows/(\d+)/\?", endpoint)
        if m:
            pk = m.group(1)
            r = self.runs.get(pk)
            if not r:
                return {}
            rec = {
                "@id": f"/api/wf/api/workflows/{pk}/",
                "name": r["name"],
                "status": r["status"],
            }
            if "step_detail=true" in endpoint:
                rec["env"] = r.get("env") or {}
                steps = []
                for n, s in (r.get("steps") or {}).items():
                    # a step value is either a bare status string or a
                    # {"status": ..., "result": {...}} dict
                    if isinstance(s, dict):
                        steps.append({"name": n, **s})
                    else:
                        steps.append({"name": n, "status": s})
                rec["steps"] = steps
            return rec
        # list fetch (children scope): ...?...&parent_wf=<pk>...
        pm = re.search(r"parent_wf=(\d+)", endpoint)
        if pm and "historical" not in endpoint:
            parent = pm.group(1)
            members = [
                {
                    "@id": f"/api/wf/api/workflows/{pk}/",
                    "name": r["name"],
                    "status": r["status"],
                }
                for pk, r in self.runs.items()
                if str(r.get("parent")) == parent
            ]
            return {"hydra:member": members}
        return {"hydra:member": []}

    def post(self, endpoint, data=None, params=None, **kw):
        # log_list keyed by task_id -> resolve to a pk
        tid = (data or {}).get("task_id") or (params or {}).get("task_id")
        pk = self.task_map.get(tid)
        if pk:
            return {"hydra:member": [{"@id": f"/api/wf/api/workflows/{pk}/"}]}
        return {"hydra:member": []}


def test_run_tree_builds_parent_child_tree():
    runs = {
        "10": {"name": "Parent", "status": "finished", "parent": None},
        "11": {"name": "Child", "status": "finished", "parent": "10"},
        "12": {"name": "Child", "status": "finished", "parent": "10"},
        "13": {"name": "Grandchild", "status": "finished", "parent": "11"},
    }
    tree = PlaybooksAPI(TreeClient(runs)).run_tree(10)
    assert tree.pk == "10" and tree.name == "Parent"
    assert {c.pk for c in tree.children} == {"11", "12"}
    grand = next(c for c in tree.children if c.pk == "11").children
    assert [g.pk for g in grand] == ["13"]


def test_run_tree_depth_caps_descent():
    runs = {
        "10": {"name": "P", "status": "finished", "parent": None},
        "11": {"name": "C", "status": "finished", "parent": "10"},
        "13": {"name": "G", "status": "finished", "parent": "11"},
    }
    api = PlaybooksAPI(TreeClient(runs))
    assert api.run_tree(10, depth=0).children == []
    one = api.run_tree(10, depth=1)
    assert [c.pk for c in one.children] == ["11"]
    assert one.children[0].children == []  # grandchild not descended


def test_run_tree_resolves_task_id():
    runs = {"10": {"name": "P", "status": "finished", "parent": None}}
    tid = "12345678-1234-1234-1234-123456789abc"
    tree = PlaybooksAPI(TreeClient(runs, task_map={tid: "10"})).run_tree(tid)
    assert tree.pk == "10"
    assert tree.task_id == tid


def test_run_tree_unresolvable_keeps_task_id():
    tid = "12345678-1234-1234-1234-123456789abc"
    tree = PlaybooksAPI(TreeClient({})).run_tree(tid)
    assert tree.pk is None
    assert tree.task_id == tid


def test_wait_for_task_returns_when_already_terminal():
    runs = {"10": {"name": "P", "status": "finished", "parent": None}}
    tree = PlaybooksAPI(TreeClient(runs)).wait_for_task("10", timeout=1, poll_interval=0)
    assert tree.pk == "10" and tree.status == "finished"


def test_wait_for_task_polls_until_terminal(monkeypatch):
    """Status flips executing -> finished after a couple of polls."""
    runs = {"10": {"name": "P", "status": "executing", "parent": None}}
    api = PlaybooksAPI(TreeClient(runs))

    calls = {"n": 0}
    real_run_tree = api.run_tree

    def flip(*a, **kw):
        calls["n"] += 1
        if calls["n"] >= 3:
            runs["10"]["status"] = "finished"
        return real_run_tree(*a, **kw)

    monkeypatch.setattr(api, "run_tree", flip)
    monkeypatch.setattr("pyfsr.api.playbooks.time.sleep", lambda _s: None)

    tree = api.wait_for_task("10", timeout=5, poll_interval=0.01)
    assert tree.status == "finished"
    assert calls["n"] == 3  # two non-terminal polls, then terminal


def test_wait_for_task_times_out():
    runs = {"10": {"name": "P", "status": "executing", "parent": None}}
    with pytest.raises(TimeoutError, match="did not finish within"):
        PlaybooksAPI(TreeClient(runs)).wait_for_task("10", timeout=0, poll_interval=0)


def test_wait_for_task_on_poll_drives_run_to_terminal(monkeypatch):
    """The callback acts on the live run (flips it terminal) — the mid-flight
    gate-driving pattern collapsed into one loop."""
    runs = {"10": {"name": "P", "status": "executing", "parent": None}}
    api = PlaybooksAPI(TreeClient(runs))
    monkeypatch.setattr("pyfsr.api.playbooks.time.sleep", lambda _s: None)

    seen = []

    def drive(progress):
        seen.append((progress.poll_count, progress.tree.status, progress.is_terminal))
        # After the 2nd poll, "answer the gate" so the run finishes.
        if progress.poll_count == 2:
            runs["10"]["status"] = "finished"

    tree = api.wait_for_task("10", timeout=5, poll_interval=0.01, on_poll=drive)
    assert tree.status == "finished"
    # WaitProgress carried a real RunNode + 1-based count; terminal poll flagged.
    assert seen[0] == (1, "executing", False)
    assert seen[-1] == (3, "finished", True)
    assert all(isinstance(c, int) for c, _, _ in seen)


def test_wait_for_task_on_poll_early_stop():
    """Returning False from on_poll stops the wait and returns the current tree."""
    runs = {"10": {"name": "P", "status": "executing", "parent": None}}
    api = PlaybooksAPI(TreeClient(runs))

    tree = api.wait_for_task("10", timeout=5, poll_interval=0, on_poll=lambda p: False)
    assert tree.status == "executing"  # returned pre-terminal, no timeout raised


def test_run_env_resolves_task_id():
    # run_env must accept a task_id (not just a numeric pk) and resolve it the
    # same way step_status does -- otherwise the uuid is sent as a pk and the
    # detail URL 500s.
    tid = "12345678-1234-1234-1234-123456789abc"
    runs = {
        "10": {
            "name": "P",
            "status": "finished",
            "parent": None,
            "steps": {"Capture": {"status": "finished", "result": {"got_severity": "High"}}},
        }
    }
    env = PlaybooksAPI(TreeClient(runs, task_map={tid: "10"})).run_env(tid)
    assert env.status == "finished"
    assert env.steps["Capture"].result == {"got_severity": "High"}


def test_step_status_returns_step_state():
    runs = {
        "10": {
            "name": "P",
            "status": "finished",
            "parent": None,
            "steps": {"StampResult": "finished", "CallChild": "finished"},
        }
    }
    api = PlaybooksAPI(TreeClient(runs))
    assert api.step_status(10, "StampResult") == "finished"
    assert api.step_status(10, "NoSuchStep") is None


# --- run_tree(steps=True): per-step I/O snapshots on the root ---------------
def test_run_tree_steps_true_enriches_root_with_snapshots():
    """steps=True fetches the root with step_detail and attaches slim
    RunStepSnapshots (name/status/result_preview) — enough to drill in without a
    separate run_env call. Children stay slim (no steps)."""
    runs = {
        "10": {
            "name": "Parent",
            "status": "finished",
            "parent": None,
            "steps": {
                "Fetch": {"status": "finished", "result": {"hits": 3}},
                "Block": {"status": "finished", "result": {"blocked": True}},
            },
        },
        "11": {"name": "Child", "status": "finished", "parent": "10"},
    }
    tree = PlaybooksAPI(TreeClient(runs)).run_tree(10, steps=True)
    assert tree.pk == "10"
    assert len(tree.steps) == 2
    by_name = {s.name: s for s in tree.steps}
    assert by_name["Fetch"].status == "finished"
    assert '"hits": 3' in (by_name["Fetch"].result_preview or "")
    assert by_name["Block"].result_preview == '{"blocked": true}'
    # The child got fetched (depth>=1) but does NOT carry step snapshots.
    assert tree.children
    assert all(c.steps == [] for c in tree.children)


def test_run_tree_steps_false_default_no_step_detail_fetch():
    """Default (steps=False) does not request step_detail — the root carries no
    snapshots, and the fetch URL omits step_detail=true."""
    runs = {
        "10": {
            "name": "P",
            "status": "finished",
            "parent": None,
            "steps": {"Fetch": {"status": "finished", "result": {"x": 1}}},
        }
    }
    client = TreeClient(runs)
    tree = PlaybooksAPI(client).run_tree(10)
    assert tree.steps == []
    # The default fetch URL has no step_detail=true (the run_env/step_status
    # paths add it; run_tree(steps=False) must not).


def test_run_tree_steps_trims_large_result_to_preview():
    """A step with a huge result gets a capped result_preview (~500 chars + …),
    not the full blob — keeps the tree lean for an agent context."""
    big = {"rows": [{"k": i} for i in range(200)]}  # ~1.5KB JSON
    runs = {
        "10": {
            "name": "P",
            "status": "finished",
            "parent": None,
            "steps": {"Hunt": {"status": "finished", "result": big}},
        }
    }
    tree = PlaybooksAPI(TreeClient(runs)).run_tree(10, steps=True)
    preview = tree.steps[0].result_preview
    assert preview is not None
    assert len(preview) <= 600  # ~500 cap + ellipsis + slack
    assert preview.endswith("…")


def test_run_tree_steps_none_result_preview_is_none():
    """A step whose result is None (e.g. an incipient/skipped step) yields a
    None preview, not the string 'null'."""
    runs = {
        "10": {
            "name": "P",
            "status": "finished",
            "parent": None,
            "steps": {"Skipped": {"status": "incipient", "result": None}},
        }
    }
    tree = PlaybooksAPI(TreeClient(runs)).run_tree(10, steps=True)
    assert tree.steps[0].result_preview is None
    assert tree.steps[0].status == "incipient"
