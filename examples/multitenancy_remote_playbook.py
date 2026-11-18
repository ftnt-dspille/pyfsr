"""Run a playbook on a dedicated tenant from the master, and prove the round trip.

The full master -> tenant flow with pyfsr, using the two playbooks in
``examples/playbooks/mt_health/``:

* ``tenant_marker.yaml``  -- "MT Health Marker": remote-executable, no inputs, creates
  one marker alert on the node it runs on. Authored on the MASTER, pushed to tenants.
* ``master_trigger.yaml`` -- "MT Health Trigger": an API-endpoint playbook on the
  master whose Trigger Tenant Playbook step runs the marker on one tenant.

Steps:

1. Compile both YAML files and deploy them to the master (non-destructive deploy).
2. Check the marker playbook is remote-executable (``remote_executable: true`` in its
   YAML; the master can only trigger a tenant copy with ``remoteExecutableFlag`` set).
3. Push the "MT Health - Tenant" collection to the tenants
   (``client.multitenancy.push_playbooks``). The push also creates the playbook
   alias and maps it on every tenant.
4. Look the alias up (``client.multitenancy.playbook_alias``).
5. For each tenant: call the trigger endpoint, wait for the master run, then wait
   for the marker alert on the tenant (master -> tenant) and on the master
   (tenant -> master replication).
6. Delete the marker alerts unless ``--keep``.

Exits non-zero when any tenant fails, so it doubles as a health check.

Usage::

    python examples/multitenancy_remote_playbook.py \\
        --master mssp-master --tenant region-a=mssp-region-a --tenant region-b=mssp-region-b

``--master`` and each ``--tenant NAME=INSTANCE`` are pyfsr instance names from
``~/.pyfsr/instances.toml``; NAME is the tenant's name on the master.
Add ``--no-deploy`` to skip steps 1-3 when the playbooks are already in place.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pyfsr.instances import InstanceRegistry

HERE = Path(__file__).parent / "playbooks" / "mt_health"
TENANT_COLLECTION = "MT Health - Tenant"
MARKER = "MT Health Marker"
ROUTE = "mt-health-trigger"
MARKER_ALERT = "MT health marker - remote run"


def _alerts_named(client, name: str) -> list[str]:
    q = {"logic": "AND", "filters": [{"field": "name", "operator": "eq", "value": name}]}
    return [a["uuid"] for a in client.post("/api/query/alerts", data=q).get("hydra:member", [])]


def _wait_new(client, name: str, before: set[str], timeout: int) -> str | None:
    end = time.time() + timeout
    while time.time() < end:
        new = set(_alerts_named(client, name)) - before
        if new:
            return sorted(new)[0]
        time.sleep(5)
    return None


def deploy(master) -> None:
    for yml in ("tenant_marker.yaml", "master_trigger.yaml"):
        res = master.workflow_collections.compile_yaml(HERE / yml)
        if not res.ok:
            raise SystemExit(f"{yml} did not compile: {res.blocking()}")
        out = master.workflow_collections.deploy(res.fsr_json)
        print(f"deploy {yml}: new={out.get('new')} updated={out.get('updated')}")
    # tenant_marker.yaml sets `remote_executable: true` (fsr_playbooks >= 0.12.1).
    wf = master.get("/api/3/workflows", params={"name": MARKER})["hydra:member"][0]
    if not wf.get("remoteExecutableFlag"):
        raise SystemExit(f"{MARKER} is still not remote-executable")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--master", required=True, help="pyfsr instance name of the master")
    p.add_argument("--tenant", action="append", required=True, metavar="NAME=INSTANCE")
    p.add_argument("--no-deploy", action="store_true", help="playbooks already deployed and pushed")
    p.add_argument("--keep", action="store_true", help="leave the marker alerts in place")
    p.add_argument("--timeout", type=int, default=180, help="seconds to wait for each marker")
    args = p.parse_args()

    reg = InstanceRegistry.load()
    master = reg.client(args.master)
    tenants = dict(t.split("=", 1) for t in args.tenant)

    if not args.no_deploy:
        deploy(master)
        print("push:", master.multitenancy.push_playbooks([TENANT_COLLECTION], list(tenants)))
    alias = master.multitenancy.playbook_alias(MARKER)

    results, cleanup = {}, []
    for name, inst in tenants.items():
        tenant = reg.client(inst)
        tid = master.multitenancy.tenant_id(name)
        r = {"tenant_id": tid, "mapped": tid in alias["tenants"]}
        before_t, before_m = set(_alerts_named(tenant, MARKER_ALERT)), set(_alerts_named(master, MARKER_ALERT))
        task = master.post(f"/api/triggers/1/{ROUTE}", data={"alias": alias["uuid"], "tenant_id": tid})["task_id"]
        r["master_run"] = master.playbooks.wait_for_task(task, timeout=args.timeout).status
        on_tenant = _wait_new(tenant, MARKER_ALERT, before_t, args.timeout)
        on_master = _wait_new(master, MARKER_ALERT, before_m, args.timeout)
        r.update(master_to_tenant=bool(on_tenant), tenant_to_master=bool(on_master))
        r["ok"] = r["mapped"] and r["master_run"] == "finished" and r["master_to_tenant"] and r["tenant_to_master"]
        results[name] = r
        cleanup += [(tenant, on_tenant), (master, on_master)]

    if not args.keep:
        for client, uuid in cleanup:
            if uuid:
                client.delete(f"/api/3/alerts/{uuid}")
    print(json.dumps(results, indent=2))
    return 0 if all(r["ok"] for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
