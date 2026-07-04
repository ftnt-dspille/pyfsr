"""Unit tests for PlaybooksAPI.diagnose_run + the diagnose_run MCP tool.

diagnose_run diffs a playbook's DEFINITION (step graph) against a RUN (executed
step statuses) so an agent can answer "did my playbook run do what I defined?"
without cross-referencing get_definition / run_env / why_failed by hand.
"""

from __future__ import annotations

from typing import Any

from pyfsr.agent import tools
from pyfsr.api.playbooks import PlaybooksAPI


def _definition(uuid: str, name: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """A /api/3/workflows/<uuid> definition payload with inlined steps."""
    return {"uuid": uuid, "name": name, "steps": steps}


def _step(name: str, type_name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "stepType": {"name": type_name}, "arguments": args}


def _run(pk: str, status: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """A /api/wf/api/workflows/<pk>/ run payload with per-step results."""
    return {
        "@id": f"/api/wf/api/workflows/{pk}/",
        "name": "PB",
        "status": status,
        "env": {},
        "steps": steps,
    }


def _run_step(name: str, status: str, result: Any = None) -> dict[str, Any]:
    return {"name": name, "status": status, "result": result}


class DiagnoseClient:
    """Routes the four endpoints diagnose_run touches:

    - GET /api/3/workflows/<uuid>            -> playbook definition (with steps)
    - GET /api/wf/api/workflows/?...         -> live run list (latest run)
    - GET /api/wf/api/historical-workflows/? -> historical run list (empty here)
    - GET /api/wf/api/workflows/<pk>/...     -> one run with step_detail
    """

    def __init__(
        self,
        *,
        definition: dict[str, Any] | None = None,
        runs: list[dict[str, Any]] | None = None,
        run_by_pk: dict[str, dict[str, Any]] | None = None,
    ):
        self.definition = definition
        self.runs = runs or []
        self.run_by_pk = run_by_pk or {}
        self.get_calls: list[tuple[str, Any]] = []
        # The MCP handlers reach the API through client.playbooks (like FortiSOAR).
        self.playbooks = PlaybooksAPI(self)

    def get(self, endpoint: str, params: Any = None, **kw: Any) -> dict[str, Any]:
        self.get_calls.append((endpoint, params))
        # Definition fetch: /api/3/workflows/<uuid>
        if endpoint.startswith("/api/3/workflows/") and "?" not in endpoint.split("/api/3/workflows/", 1)[1]:
            return self.definition or {}
        # Single run fetch: /api/wf/api/workflows/<pk>/?format=json&step_detail=true
        if endpoint.startswith("/api/wf/api/historical-workflows/"):
            tail = endpoint.split("/api/wf/api/historical-workflows/", 1)[1]
            if tail.startswith("?"):
                return {"hydra:member": []}
            return {}
        if endpoint.startswith("/api/wf/api/workflows/"):
            tail = endpoint.split("/api/wf/api/workflows/", 1)[1]
            if tail.startswith("?"):  # list
                return {"hydra:member": self.runs}
            # single run
            pk = tail.split("/")[0]
            return self.run_by_pk.get(pk, {})
        return {}

    def post(self, endpoint: str, data: Any = None, params: Any = None, **kw: Any) -> dict[str, Any]:
        return {}


# --- happy path: run completed, all defined steps reached -------------------
def test_diagnose_run_completed_all_steps_reached():
    client = DiagnoseClient(
        definition=_definition(
            "u1",
            "Block IP",
            [
                _step("Start", "cybersponse.abstract_trigger"),
                _step("Fetch", "FindRecords"),
                _step("Block", "Connectors", connector="fortigate-firewall"),
            ],
        ),
        runs=[_run("1001", "finished", [])],
        run_by_pk={
            "1001": _run(
                "1001",
                "finished",
                [
                    _run_step("Start", "finished"),
                    _run_step("Fetch", "finished"),
                    _run_step("Block", "finished"),
                ],
            )
        },
    )
    out = PlaybooksAPI(client).diagnose_run(playbook_uuid="u1")
    assert out["verdict"] == "completed"
    assert out["run"]["pk"] == "1001"
    assert out["run"]["status"] == "finished"
    assert [s["name"] for s in out["definition_steps"]] == ["Start", "Fetch", "Block"]
    assert all(s["status"] == "finished" for s in out["definition_steps"])
    assert out["not_reached"] == []
    assert out["executed_not_defined"] == []
    assert out["failing_step"] is None
    assert "3/3" in out["summary"]


# --- failed mid-run: a defined step never reached --------------------------
def test_diagnose_run_failed_step_not_reached():
    client = DiagnoseClient(
        definition=_definition(
            "u1",
            "Block IP",
            [
                _step("Start", "cybersponse.abstract_trigger"),
                _step("Fetch", "FindRecords"),
                _step("Block", "Connectors"),
            ],
        ),
        runs=[_run("1009", "failed", [])],
        run_by_pk={
            "1009": _run(
                "1009",
                "failed",
                [
                    _run_step("Start", "finished"),
                    _run_step("Fetch", "failed", result={"Error message": "connector down"}),
                    # "Block" never ran — incipient (engine never reached it)
                ],
            )
        },
    )
    out = PlaybooksAPI(client).diagnose_run(playbook_uuid="u1")
    assert out["verdict"] == "failed"
    assert out["failing_step"] == "Fetch"
    assert out["error_message"] == "connector down"
    assert out["not_reached"] == ["Block"]
    assert "2/3" in out["summary"]
    assert "failed at 'Fetch'" in out["summary"]


# --- no run: playbook found but never executed -----------------------------
def test_diagnose_run_no_executions():
    client = DiagnoseClient(
        definition=_definition("u1", "Block IP", [_step("Start", "cybersponse.abstract_trigger")]),
        runs=[],  # no executions
    )
    out = PlaybooksAPI(client).diagnose_run(playbook_uuid="u1")
    assert out["verdict"] == "no_run"
    assert out["run"] is None
    assert out["not_reached"] == ["Start"]  # the one defined step never ran
    assert out["definition_steps"][0]["name"] == "Start"


# --- drift: run recorded a step the current definition lacks ---------------
def test_diagnose_run_detects_drift_step_not_in_definition():
    client = DiagnoseClient(
        definition=_definition(
            "u1",
            "PB",
            [_step("Start", "cybersponse.abstract_trigger"), _step("Block", "Connectors")],
        ),
        runs=[_run("1001", "finished", [])],
        run_by_pk={
            "1001": _run(
                "1001",
                "finished",
                [
                    _run_step("Start", "finished"),
                    _run_step("Block", "finished"),
                    # The run executed a step the current definition doesn't have
                    # (e.g. an older version of the playbook).
                    _run_step("Legacy Notify", "finished"),
                ],
            )
        },
    )
    out = PlaybooksAPI(client).diagnose_run(playbook_uuid="u1")
    assert out["verdict"] == "completed"
    assert [e["name"] for e in out["executed_not_defined"]] == ["Legacy Notify"]


# --- explicit run arg skips the latest-run lookup --------------------------
def test_diagnose_run_explicit_run_arg():
    client = DiagnoseClient(
        definition=_definition("u1", "PB", [_step("Start", "cybersponse.abstract_trigger")]),
        runs=[_run("2000", "finished", [])],  # should be ignored
        run_by_pk={
            "3000": _run("3000", "finished", [_run_step("Start", "finished")]),
        },
    )
    out = PlaybooksAPI(client).diagnose_run(playbook_uuid="u1", run="3000")
    assert out["run"]["pk"] == "3000"
    # The latest-run list endpoint was NOT hit (only definition + the explicit run).
    list_gets = [c for c in client.get_calls if c[0].startswith("/api/wf/api/workflows/?")]
    assert list_gets == []


# --- no definition: unresolvable playbook ----------------------------------
def test_diagnose_run_no_definition_when_uuid_missing():
    # diagnose_run with neither playbook nor playbook_uuid -> _resolve_uuid returns None
    client = DiagnoseClient(definition=None)
    out = PlaybooksAPI(client).diagnose_run(playbook="Ghost")
    assert out["verdict"] == "no_definition"
    assert out["definition_steps"] == []


# --- MCP dispatch surfaces the structured diff -----------------------------
def test_dispatch_diagnose_run():
    client = DiagnoseClient(
        definition=_definition(
            "u1",
            "Block IP",
            [_step("Start", "cybersponse.abstract_trigger"), _step("Block", "Connectors")],
        ),
        runs=[_run("1042", "failed", [])],
        run_by_pk={
            "1042": _run(
                "1042",
                "failed",
                [
                    _run_step("Start", "finished"),
                    _run_step("Block", "failed", result={"Error message": "boom"}),
                ],
            )
        },
    )
    out = tools.dispatch(client, "diagnose_run", {"playbook_uuid": "u1"})
    assert out["verdict"] == "failed"
    assert out["failing_step"] == "Block"
    assert out["error_message"] == "boom"
    assert out["not_reached"] == []
    # The tool is registered + documented for an agent.
    spec = {t["name"]: t for t in tools.tool_schemas()}
    assert "diagnose_run" in spec
    assert "verdict" in (spec["diagnose_run"]["description"] or "")
