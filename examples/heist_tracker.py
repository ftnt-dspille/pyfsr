"""End-to-end pyfsr demo: build a module schema, wire permissions, deploy an
on-create playbook, and watch it fire.

A playful "heist tracker" that exercises the whole SDK surface in one run:

  1. **Modules** — create two linked modules, `crew` and `heists`
     (many-to-many; the reverse field on `crew` is auto-created).
  2. **Publish** — commit the schema (appliance-wide migrate).
  3. **Permissions** — grant CRUD on the new modules to a role so the API can
     write records.
  4. **Playbook** — deploy a YAML playbook that triggers *on create* of a
     `heists` record and stamps its `status` to "Briefed".
  5. **Trigger** — create crew + a heist record, then poll until the playbook
     flips the status — proof the trigger fired.

The script is idempotent: modules that already exist are reused (and missing
fields are added), and the playbook is deployed with ``replace=True``.

Usage:
    python examples/heist_tracker.py \
        --server fortisoar.example.com --port 13000 --user csadmin --password '...'

Or set FSR_BASE_URL / FSR_USERNAME / FSR_PASSWORD and run with no args.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from pyfsr import FortiSOAR, Query

HERE = Path(__file__).resolve().parent
PLAYBOOK_YAML = HERE / "playbooks" / "heist_intake.yaml"
ADMIN_ROLE = "Full App Permissions"


def banner(msg: str) -> None:
    print(f"\n{'=' * 4} {msg} {'=' * 4}")


def connect(args: argparse.Namespace) -> FortiSOAR:
    base = args.server or os.environ.get("FSR_BASE_URL")
    if not base:
        sys.exit("no server: pass --server or set FSR_BASE_URL")
    if not base.startswith("http"):
        base = f"https://{base}" + (f":{args.port}" if args.port else "")
    return FortiSOAR(
        base_url=base,
        username=args.user or os.environ.get("FSR_USERNAME", "csadmin"),
        password=args.password or os.environ.get("FSR_PASSWORD"),
        verify_ssl=False,
        suppress_insecure_warnings=True,
    )


def ensure_field(admin, module: str, field: dict) -> None:
    """Add a field to a staged/published module if it isn't there yet."""
    have = {a["name"] for a in admin.get_staging(module)["attributes"]}
    if field["name"] not in have:
        admin.add_field(module, field)
        print(f"  + added field {field['name']!r} to {module}")


def ensure_modules(admin) -> bool:
    """Create crew + heists if absent; ensure their fields. Returns True if a
    publish is needed (any staged change was made)."""
    changed = False

    if admin.is_published("crew"):
        print("crew already published — reusing")
    else:
        banner("create_module crew")
        admin.create_module(
            "crew",
            label="Crew Member",
            plural="Crew",
            fields=[
                admin.text_field("alias", required=True, grid_column=True),
                admin.picklist_field("specialty", "AlertType"),
                admin.checkbox_field("trustworthy"),
            ],
            record_uniqueness=["alias"],
        )
        changed = True

    if admin.is_published("heists"):
        print("heists already published — reusing")
    else:
        banner("create_module heists (linked to crew)")
        admin.create_module(
            "heists",
            label="Heist",
            plural="Heists",
            fields=[
                admin.text_field("codename", required=True, grid_column=True),
                admin.text_field("target", grid_column=True),
                admin.text_field("status"),
                admin.integer_field("takeUsd"),
                admin.datetime_field("goTime"),
                admin.relationship_field("crew", "crew", label="Crew"),
            ],
        )
        changed = True

    # The playbook stamps `status`; make sure it exists even on a pre-existing
    # heists module from an earlier partial run.
    if admin.is_published("heists"):
        before = {a["name"] for a in admin.get_staging("heists")["attributes"]}
        ensure_field(admin, "heists", admin.text_field("status"))
        if "status" not in before:
            changed = True

    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server")
    ap.add_argument("--port", type=int)
    ap.add_argument("--user")
    ap.add_argument("--password")
    ap.add_argument("--keep", action="store_true", help="leave demo records in place")
    args = ap.parse_args()

    c = connect(args)
    admin = c.modules_admin

    # 1 + 2. Modules + publish ------------------------------------------------
    if ensure_modules(admin):
        banner("publish (appliance-wide migrate; rides out the ~30-60s outage)")
        admin.publish()
    print("published crew:", admin.is_published("crew"), "| heists:", admin.is_published("heists"))

    # confirm the auto-created reverse field
    crew_fields = [a["name"] for a in admin.get_published("crew")["attributes"]]
    print("crew fields:", crew_fields, "| reverse 'heists' present:", "heists" in crew_fields)

    # 3. Permissions ----------------------------------------------------------
    banner(f"grant CRUD on crew + heists to {ADMIN_ROLE!r}")
    for mod in ("crew", "heists"):
        c.roles.grant_module_permissions(ADMIN_ROLE, module=mod)
        print(f"  granted {mod}")

    # 3b. Connector config ----------------------------------------------------
    # The playbook's code_snippet step runs through the `code-snippet` connector,
    # which needs a configuration on the box. default_config() fills the schema
    # (incl. onchange-revealed fields); we set allow_imports=True so the pure-
    # arithmetic snippet runs without the import-restriction machinery.
    banner("ensure the code-snippet connector is configured")
    try:
        c.connectors.upsert_configuration(
            "code-snippet",
            {"allow_imports": True},
            name="default",
            default=True,
            validate=False,
        )
        print("  code-snippet config ready")
    except Exception as exc:  # noqa: BLE001 — keep the demo going if it's pre-set
        print(f"  (skipped: {exc})")

    # 4. Playbook -------------------------------------------------------------
    banner("deploy on-create playbook (YAML)")
    created = c.workflow_collections.import_from_yaml(str(PLAYBOOK_YAML), replace=True)
    for col in created:
        print(f"  collection {col['name']} ({col['uuid']})")

    # 5. Records → trigger ----------------------------------------------------
    banner("create crew + a heist (this fires the on-create playbook)")

    def crew_member(alias: str) -> dict:
        """Find-or-create a crew member by their (unique) alias."""
        existing = c.records("crew").first(Query(module="crew").eq("alias", alias), raw=True)
        if existing:
            print(f"  reusing crew {alias!r}")
            return existing
        return c.records("crew").create({"alias": alias, "trustworthy": True})

    danny = crew_member("The Brains")
    linus = crew_member("Light Fingers")
    job = c.records("heists").create(
        {
            "codename": "Operation Cannoli",
            "target": "Bellagio Vault",
            "takeUsd": 150_000_000,
            "crew": [danny["@id"], linus["@id"]],
        }
    )
    print(f"  heist {job['codename']} created (status={job.get('status')!r})")

    banner("wait for the playbook to run (set_variable -> code -> delay -> decision -> update) and stamp status")
    deadline = time.monotonic() + 90
    final = None
    while time.monotonic() < deadline:
        rec = c.records("heists").get(job["uuid"])
        if rec.get("status"):
            final = rec["status"]
            break
        time.sleep(3)

    # takeUsd 150M > 1M, so the decision routes to "Authorized"; a smaller
    # score would land on "Briefed". Either proves the trigger fired and the
    # branch ran. (The playbook then pauses at the manual_input 'Final Go Call'.)
    if final in ("Authorized", "Briefed"):
        print(
            f"  ✅ playbook fired — decision branch stamped status {final!r} (run now waiting at the manual-input step)"
        )
    elif final:
        print(f"  status changed to {final!r}")
    else:
        print(
            "  ⚠️  status still empty after 90s — check the playbook run log "
            "(pyfsr playbook logs) and that the collection is active"
        )

    # relationship reads both ways
    banner("bidirectional link")
    hj = c.records("heists").get(job["uuid"], relationships=True)
    print("  heist -> crew:", [m.get("alias") for m in hj.get("crew", [])])
    dr = c.records("crew").get(danny["uuid"], relationships=True)
    print("  crew -> heists:", [m.get("codename") for m in dr.get("heists", [])])

    if not args.keep:
        for rec, mod in ((job, "heists"), (danny, "crew"), (linus, "crew")):
            try:
                c.records(mod).delete(rec["uuid"])
            except Exception:
                pass
        print(
            "\n(cleaned up demo records; pass --keep to retain them. "
            "Published modules persist — there is no API to delete them.)"
        )

    print("\nDONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
