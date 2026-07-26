#!/usr/bin/env python
"""End-to-end proof of `connectors.data_ingest_wizard` against FortiSIEM.

Drives the whole *Configure Data Ingestion* flow headlessly and then verifies
the result the way the UI would read it back:

    configure connector -> health Available -> per-config playbook collection
    -> cloned+rewritten ingestion playbooks -> periodic task -> data-import record

No credentials live in this file. Point at a box with its env file and pass the
FortiSIEM creds via FSM_* vars::

    set -a; . .env.fsr-ga; set +a
    FSM_SERVER=https://<fortisiem-host>:<port> \
    FSM_USERNAME=<user> FSM_PASSWORD='<password>' FSM_ORGANIZATION=Super \
      .venv/bin/python examples/connectors/fortisiem_data_ingest_wizard.py

    ... --dry-run     resolve and report, write nothing
    ... --cleanup     tear down what a previous run created

Exits non-zero if any stage fails, so it works as a live regression gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings

warnings.filterwarnings("ignore")

from pyfsr import FortiSOAR  # noqa: E402

CONNECTOR = "fortinet-fortisiem"
CONFIG_NAME = "ingest-proof"
CRON = "*/15 * * * *"


def _client() -> FortiSOAR:
    base = os.environ.get("FSR_BASE_URL")
    if not base:
        sys.exit("FSR_BASE_URL is not set — source the box's env file first")
    return FortiSOAR(
        base_url=base,
        username=os.environ.get("FSR_USERNAME"),
        password=os.environ.get("FSR_PASSWORD"),
        verify_ssl=False,
    )


def _fsm_config() -> dict:
    """Build the FortiSIEM connector config from FSM_* env vars."""
    user, pw = os.environ.get("FSM_USERNAME"), os.environ.get("FSM_PASSWORD")
    server = os.environ.get("FSM_SERVER")
    if not (server and user and pw):
        sys.exit("FSM_SERVER, FSM_USERNAME and FSM_PASSWORD are required (see the module docstring)")
    return {
        "server": server,
        "fsm_type": os.environ.get("FSM_TYPE", "FortiSIEM"),
        "username": user,
        "password": pw,
        "organization": os.environ.get("FSM_ORGANIZATION", "Super"),
        "verify_ssl": os.environ.get("FSM_VERIFY_SSL", "false").lower() == "true",
    }


def _step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}")


def cleanup(client: FortiSOAR) -> int:
    """Remove the schedule, collection, and configuration a previous run made."""
    config_id = None
    for summary in client.connectors.configurations(CONNECTOR):
        if summary.name == CONFIG_NAME:
            config_id = summary.config_id
    if not config_id:
        print(f"nothing to clean up — no {CONFIG_NAME!r} configuration on {CONNECTOR}")
        return 0
    for meta in client.connectors.ingestion_metadata(config_id):
        if meta.schedule_id:
            try:
                client.delete(f"/api/wf/api/scheduled/{meta.schedule_id}/", params={"format": "json"})
                print(f"  deleted schedule {meta.schedule_id[:24]}…")
            except Exception as exc:  # pragma: no cover - best-effort teardown
                print(f"  schedule delete failed: {exc}")
    try:
        client.workflow_collections.delete(config_id, hard=True)
        print(f"  deleted ingestion collection {config_id}")
    except Exception as exc:
        print(f"  collection delete failed: {exc}")
    client.connectors.delete_configuration(config_id)
    print(f"  deleted configuration {CONFIG_NAME!r} ({config_id})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="resolve and report; write nothing")
    ap.add_argument("--cleanup", action="store_true", help="tear down a previous run and exit")
    ap.add_argument("--cron", default=CRON, help=f"ingestion cron (default {CRON!r})")
    ap.add_argument(
        "--skip-config",
        action="store_true",
        help="reuse an existing configuration instead of writing one (no FSM_* needed)",
    )
    args = ap.parse_args()

    client = _client()
    if args.cleanup:
        return cleanup(client)

    _step(1, f"connector {CONNECTOR}")
    version = client.connectors.resolve_version(CONNECTOR)
    if not version:
        sys.exit(f"{CONNECTOR} is not installed on this box")
    deps = client.connectors.dependencies_status(CONNECTOR, version=version)
    print(f"    version={version} dependencies_installed={deps.dependencies_installed}")

    if args.skip_config:
        summaries = client.connectors.configurations(CONNECTOR)
        if not summaries:
            sys.exit(f"--skip-config given but {CONNECTOR} has no configuration")
        config_name = next((s.name for s in summaries if s.default), summaries[0].name)
        print(f"    reusing configuration {config_name!r}")
    else:
        _step(2, f"upsert configuration {CONFIG_NAME!r}")
        cfg = client.connectors.upsert_configuration(CONNECTOR, _fsm_config(), name=CONFIG_NAME, default=True)
        config_name = CONFIG_NAME
        print(f"    config_id={cfg.config_id}")

    _step(3, "health check (the UI gates ingestion on this)")
    health = client.connectors.healthcheck(CONNECTOR, version=version)
    print(f"    status={health.status!r}")

    _step(4, "sample ingestion playbooks")
    pbs = client.connectors.ingestion_playbooks(CONNECTOR, version=version)
    for role in ("fetch", "ingest", "create", "update"):
        pb = getattr(pbs, role)
        print(f"    {role:<7}: {(pb or {}).get('name')}")
    if pbs.ingest is None:
        sys.exit(f"{CONNECTOR} ships no #ingest playbook — nothing to schedule")

    _step(5, f"data_ingest_wizard(cron={args.cron!r}, dry_run={args.dry_run})")
    result = client.connectors.data_ingest_wizard(
        CONNECTOR,
        config=config_name,
        cron=args.cron,
        dry_run=args.dry_run,
    )
    print(json.dumps(result.to_dict(exclude_none=True), indent=2, default=str)[:1200])
    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    _step(6, "verify the way the UI reads it back")
    ok = True

    collection = client.workflow_collections.get(result.collection_uuid)
    print(f"    collection      : {collection.name}")

    built = client.connectors.ingestion_playbooks(CONNECTOR, collection=result.collection_uuid, version=version)
    if built.ingest is None:
        print("    FAIL: cloned collection has no #ingest playbook")
        ok = False
    else:
        print(f"    ingest playbook : {built.ingest.get('name')} (active={built.ingest.get('isActive')})")

    for pb in result.playbooks:
        definition = client.playbooks.get_definition(pb["uuid"], relationships=True).to_dict()
        bound = [s for s in (definition.get("steps") or []) if (s.get("arguments") or {}).get("connector") == CONNECTOR]
        wrong = [s for s in bound if (s.get("arguments") or {}).get("config") != result.config_id]
        if wrong:
            print(f"    FAIL: {pb['name']}: {len(wrong)} step(s) not bound to this config")
            ok = False
        else:
            print(f"    bound steps     : {pb['name']} -> {len(bound)} step(s) on config_id")

    metadata = client.connectors.ingestion_metadata(result.config_id)
    if not metadata:
        print("    FAIL: no data-import record — the UI will show ingestion as unconfigured")
        ok = False
    else:
        meta = metadata[-1]
        print(f"    data-import     : {meta.name}")
        if meta.schedule_id != result.schedule_id:
            print(f"    FAIL: metadata scheduleId {meta.schedule_id!r} != {result.schedule_id!r}")
            ok = False

    if result.schedule_id:
        task = client.get(f"/api/wf/api/scheduled/{result.schedule_id}/", params={"format": "json"})
        print(f"    schedule        : {task.get('name')} enabled={task.get('enabled')} {task.get('crontab')}")
        if (task.get("kwargs") or {}).get("wf_iri") != result.ingest_playbook_iri:
            print("    FAIL: schedule does not point at the cloned ingest playbook")
            ok = False

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
