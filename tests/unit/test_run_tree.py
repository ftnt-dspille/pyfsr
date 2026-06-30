"""Unit tests for PlaybooksAPI.run_tree() and step_status()."""

import re

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
                rec["env"] = {}
                rec["steps"] = [{"name": n, "status": s} for n, s in (r.get("steps") or {}).items()]
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
