"""Create a harmless playbook collection with pyfsr and verify it round-trips.

This is meant as a live-instance smoke test for playbook creation and
execution. It creates a single collection containing a single safe workflow:

    Start -> Mark Result

The script reads the current playbook logging settings, can optionally enable
debug logging, then creates the collection, triggers the playbook, waits for the
run to finish, fetches it back, and validates that the workflow is present. By
default it hard-deletes the collection at the end so the run is safe to repeat.

Usage:
    python examples/create_safe_playbook.py \
        --host fortisoar.example.com --user csadmin --password '...' --port 13002

Environment variables:
    FSR_BASE_URL   base URL, e.g. https://fsr.example.com:13002
    FSR_USERNAME   username
    FSR_PASSWORD   password
    FSR_PORT       optional port override
    ENABLE_DEBUG_PLAYBOOK_LOGGING=1  enable global playbook debug logging
    KEEP_COLLECTION=1                leave the created collection in place
"""

from __future__ import annotations

import argparse
import os
import uuid
from typing import Any

from pyfsr import FortiSOAR

CUSTOM_PLAYBOOK_ORIGIN_IRI = "/api/3/picklists/15c1e8c9-22bf-4e66-8fbb-0a502d4a4a3f"
# Fallbacks copied from a known-good export on the same appliance family.
FALLBACK_START_STEP_TYPE = "/api/3/workflow_step_types/b348f017-9a94-471f-87f8-ce88b6a7ad62"
FALLBACK_SAFE_STEP_TYPE = "/api/3/workflow_step_types/04d0cf46-b6a8-42c4-8683-60a7eaa69e8f"
SAFE_PLAYBOOK_RESULT = "safe-playbook-ok"


def _pick_step_type(rows: list[dict[str, Any]], *needles: str) -> dict[str, Any]:
    wanted = [n.lower() for n in needles]
    for row in rows:
        haystack = " ".join(
            str(row.get(key) or "") for key in ("name", "displayName", "description")
        ).lower()
        if any(needle in haystack for needle in wanted):
            return row
    available = ", ".join(
        sorted({str(row.get("displayName") or row.get("name") or "?") for row in rows})[:20]
    )
    raise RuntimeError(f"could not find step type for {needles!r}; available: {available}")


def _record_iri(row: dict[str, Any], resource: str) -> str:
    iri = row.get("@id") or row.get("iri")
    if isinstance(iri, str) and iri.strip():
        return iri
    uuid_str = row.get("uuid")
    if isinstance(uuid_str, str) and uuid_str.strip():
        return f"/api/3/{resource}/{uuid_str.strip()}"
    raise RuntimeError(f"step type row is missing both @id and uuid: {row!r}")


def _resolve_step_type_iri(rows: list[dict[str, Any]], fallback: str, *needles: str) -> str:
    try:
        return _record_iri(_pick_step_type(rows, *needles), "workflow_step_types")
    except RuntimeError:
        return fallback


def _step_iri(uuid_str: str) -> str:
    return f"/api/3/workflow_steps/{uuid_str}"


def _build_workflow(collection_uuid: str, start_type: str, safe_type: str) -> dict[str, Any]:
    workflow_uuid = str(uuid.uuid4())
    start_step_uuid = str(uuid.uuid4())
    safe_step_uuid = str(uuid.uuid4())
    route_uuid = str(uuid.uuid4())

    return {
        "@type": "Workflow",
        "triggerLimit": None,
        "name": "pyfsr safe playbook smoke test",
        "aliasName": None,
        "tag": "",
        "description": "Safe smoke-test workflow created by pyfsr.",
        "isActive": False,
        "debug": False,
        "singleRecordExecution": False,
        "remoteExecutableFlag": False,
        "parameters": [],
        "synchronous": False,
        "collection": f"/api/3/workflow_collections/{collection_uuid}",
        "triggerStep": _step_iri(start_step_uuid),
        "steps": [
            {
                "@type": "WorkflowStep",
                "name": "Start",
                "description": None,
                "arguments": {},
                "status": None,
                "top": "120",
                "left": "200",
                "stepType": start_type,
                "group": None,
                "uuid": start_step_uuid,
                "isEditable": True,
                "class": "trigger-step",
            },
            {
                "@type": "WorkflowStep",
                "name": "Mark Result",
                "description": None,
                "arguments": {
                    "playbook_result": SAFE_PLAYBOOK_RESULT,
                    "playbook_collection": collection_uuid,
                },
                "status": None,
                "top": "220",
                "left": "200",
                "stepType": safe_type,
                "group": None,
                "uuid": safe_step_uuid,
                "isEditable": True,
            },
        ],
        "routes": [
            {
                "@type": "WorkflowRoute",
                "name": "start->mark-result",
                "targetStep": _step_iri(safe_step_uuid),
                "sourceStep": _step_iri(start_step_uuid),
                "label": None,
                "isExecuted": False,
                "group": None,
                "uuid": route_uuid,
            }
        ],
        "groups": [],
        "priority": None,
        "playbookOrigin": CUSTOM_PLAYBOOK_ORIGIN_IRI,
        "isEditable": True,
        "isPrivate": False,
        "recordTags": [],
        "uuid": workflow_uuid,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("FSR_BASE_URL", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FSR_PORT", "443")))
    parser.add_argument("--user", default=os.environ.get("FSR_USERNAME", "csadmin"))
    parser.add_argument("--password", default=os.environ.get("FSR_PASSWORD"))
    parser.add_argument(
        "--keep",
        action="store_true",
        default=os.environ.get("KEEP_COLLECTION", "").lower() in {"1", "true", "yes"},
        help="leave the created collection in place",
    )
    parser.add_argument(
        "--enable-debug-playbook-logging",
        action="store_true",
        default=os.environ.get("ENABLE_DEBUG_PLAYBOOK_LOGGING", "").lower() in {"1", "true", "yes"},
        help="turn on global playbook debug logging before the smoke test",
    )
    args = parser.parse_args()

    if not args.password:
        raise SystemExit("set --password or FSR_PASSWORD")

    client = FortiSOAR(
        args.host,
        auth=(args.user, args.password),
        verify_ssl=False,
        suppress_insecure_warnings=True,
        port=args.port,
    )

    public_values = client.system_settings.get_public_values()
    workflow_log_config = public_values.get("workflow_log_config", {})
    playbook_logs = (public_values.get("playbook") or {}).get("logs", {})
    print("current workflow_log_config:", workflow_log_config)
    print("current playbook.logs       :", playbook_logs)

    if args.enable_debug_playbook_logging:
        updated = client.system_settings.set_playbook_debug_logging(
            True, allow_playbook_override=False
        )
        print(
            "updated workflow_log_config:",
            updated.get("publicValues", {}).get("workflow_log_config"),
        )

    try:
        step_types = client.records("workflow_step_types").list(limit=5000, raw=True).members
    except Exception as exc:  # noqa: BLE001 - discovery is optional; fall back to known UUIDs
        print("step type discovery failed :", exc.__class__.__name__)
        step_types = []
    start_type = _resolve_step_type_iri(step_types, FALLBACK_START_STEP_TYPE, "start")
    safe_type = _resolve_step_type_iri(
        step_types, FALLBACK_SAFE_STEP_TYPE, "prepare inputs", "set variable", "manual"
    )
    print("resolved step types        :", start_type, safe_type)

    collection_uuid = str(uuid.uuid4())
    workflow = _build_workflow(
        collection_uuid,
        start_type,
        safe_type,
    )
    collection_name = f"pyfsr safe playbook {collection_uuid[:8]}"

    created = client.workflow_collections.create(
        collection_name,
        description="Safe smoke-test collection created by pyfsr.",
        visible=True,
        workflows=[workflow],
        uuid=collection_uuid,
        record_tags=["pyfsr-safe-example"],
    )
    print("created collection uuid    :", created["uuid"])

    definition = client.playbooks.get_definition(workflow["uuid"])
    print("definition name            :", definition.get("name"))
    print("definition collection      :", definition.get("collection"))
    if definition.get("name") != workflow["name"]:
        raise RuntimeError("playbook definition name did not round-trip")

    scoped_defs = client.playbooks.list(collection=created["uuid"], relationships=True)
    print("scoped playbook count      :", len(scoped_defs))
    if len(scoped_defs) != 1:
        raise RuntimeError(f"expected one workflow in the collection, got {len(scoped_defs)}")
    if scoped_defs[0].get("uuid") != workflow["uuid"]:
        raise RuntimeError("scoped playbook lookup returned the wrong workflow")

    print("triggering playbook        :", workflow["name"])
    run = client.playbooks.trigger(workflow["name"], follow=True, timeout=120, interval=2)
    print("trigger status             :", run.get("status"))
    print("trigger task_id            :", run.get("task_id"))
    print("trigger run pk             :", run.get("pk"))
    if run.get("error_message"):
        print("trigger error              :", run.get("error_message"))

    run_env = client.playbooks.run_env(run["pk"])
    final_step = run_env["steps"].get("Mark Result") or {}
    result_value = run_env["env"].get("playbook_result")
    print("run env result             :", result_value)
    print("final step status          :", final_step.get("status"))
    print("final step result keys     :", sorted((final_step.get("result") or {}).keys()))
    if result_value != SAFE_PLAYBOOK_RESULT:
        raise RuntimeError(
            f"playbook result mismatch: expected {SAFE_PLAYBOOK_RESULT!r}, got {result_value!r}"
        )
    if final_step.get("status") != "finished":
        raise RuntimeError(f"final step did not finish: {final_step!r}")

    fetched = client.workflow_collections.get(created["uuid"])
    workflows = fetched.get("workflows") or []
    print("fetched workflow count     :", len(workflows))
    if not workflows:
        raise RuntimeError("collection came back without workflows")
    print("fetched workflow name      :", workflows[0].get("name"))
    print("fetched workflow active    :", workflows[0].get("isActive"))

    run_count = len(client.playbooks.runs(playbook=workflow["name"], limit=5))
    print("run history entries        :", run_count)

    if args.keep:
        print("keeping collection         :", created["uuid"])
        return

    client.workflow_collections.delete(created["uuid"])
    print("deleted collection         :", created["uuid"])


if __name__ == "__main__":
    main()
