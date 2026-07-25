#!/usr/bin/env python
"""Export Wizard → Import Wizard round-trip for a custom module.

Drives both FortiSOAR configuration wizards end to end and proves that a custom
module survives a full export / delete / re-import cycle:

  1. Setup      — create a throwaway module (name + note fields) and one record.
  2. EXPORT     — the Export Wizard: ``export_config.create_template`` +
                  ``export_by_template_name`` produce a ``.zip`` bundle
                  (``modules/<name>/mmd.json`` + ``records/<name>/*.json``).
  3. DELETE     — FortiSOAR has no true module DELETE: the API only discards the
                  staging draft, and the physical Postgres tables persist as
                  orphans that collide on re-create. So teardown uses
                  ``delete_module(drop_orphan_tables=True)``, which runs the
                  backend ``DROP TABLE`` over SSH — the appliance is auto-resolved
                  from the client's own instance alias, so the caller never
                  touches ``Facts``.
  4. IMPORT     — the Import Wizard:
                  ``workflow_collections.import_export_zip(create_modules=True)``
                  recreates the module *from the export's own mmd.json* and
                  re-creates the record.
  5. VERIFY     — the module is published again and the record is back.

REST credentials come from ``FSR_*`` env vars (or ``--instance <alias>`` from
``~/.pyfsr/instances.toml``). The Postgres drop reaches the appliance via the
instance's ``[instances.<alias>.appliance]`` SSH profile — auto-resolved from
the REST client's alias, or pass ``--appliance-instance <alias>`` when the SSH
profile lives under a different alias. No box details live in this file.

    FSR_BASE_URL=https://fortisoar.example.com FSR_USERNAME=... FSR_PASSWORD=... \\
        python export_import_wizard_roundtrip.py --instance <alias>

    # when the appliance SSH profile is under a different alias than the REST one:
    python export_import_wizard_roundtrip.py --instance <rest> --appliance-instance <ssh>
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

warnings.filterwarnings("ignore")

from pyfsr import FortiSOAR, Query  # noqa: E402
from pyfsr.api.export_config import ExportTemplate  # noqa: E402
from pyfsr.appliance import Appliance  # noqa: E402


def build_client(args: argparse.Namespace) -> FortiSOAR:
    """REST client from --instance (registry) or FSR_* env vars."""
    if args.instance:
        from pyfsr.instances import InstanceRegistry

        return InstanceRegistry.load().client(args.instance)
    base = os.environ.get("FSR_BASE_URL")
    if not base:
        raise SystemExit("set FSR_BASE_URL/FSR_USERNAME/FSR_PASSWORD or pass --instance")
    if os.environ.get("FSR_API_KEY"):
        return FortiSOAR(base_url=base, api_key=os.environ["FSR_API_KEY"])
    return FortiSOAR(
        base_url=base,
        username=os.environ["FSR_USERNAME"],
        password=os.environ["FSR_PASSWORD"],
    )


def _full_delete(client: FortiSOAR, drop: object, module: str) -> None:
    """Delete a module for real — discard staging + drop the orphan Postgres tables.

    ``drop`` is passed straight to ``delete_module(drop_orphan_tables=…)``:
    ``True`` auto-resolves the appliance from the client's own instance alias,
    or an :class:`Appliance` supplies SSH access explicitly. Either way the
    caller never touches ``Facts``.
    """
    res = client.modules_admin.delete_module(
        module,
        detach_relationships=True,
        drop_orphan_tables=drop,  # True → auto-resolve, or an Appliance
        remove_from_nav=True,
    )
    print(f"  deleted {module!r}; dropped tables: {res.get('dropped_tables')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", help="REST instance alias from ~/.pyfsr/instances.toml (else FSR_* env)")
    ap.add_argument(
        "--appliance-instance",
        help="appliance SSH profile alias for the Postgres drop, when it differs from "
        "the REST instance. Omit to auto-resolve from the REST client's own alias.",
    )
    ap.add_argument("--module", default="pyfsr_wizard_demo", help="throwaway module type/table name")
    ap.add_argument("--keep", action="store_true", help="leave the recreated module on the box")
    args = ap.parse_args()

    client = build_client(args)
    module = args.module

    # How the Postgres orphan-table drop reaches the appliance — the caller
    # never handles Facts. Either an explicit Appliance (when the SSH profile
    # alias differs from the REST one), or True to auto-resolve from the REST
    # client's own instance alias.
    drop: object = Appliance(instance=args.appliance_instance) if args.appliance_instance else True

    # --- 0. clean slate + setup source content ---------------------------- #
    print("=== 0. setup ===")
    if client.modules_admin.get_published(module) or client.modules_admin.get_staging(module):
        print(f"  {module!r} already exists — removing first")
        _full_delete(client, drop, module)

    # Grant to a real admin-like role so the new module's records are accessible.
    roles = client.roles.list()
    role_names = [r.get("name") if isinstance(r, dict) else getattr(r, "name", None) for r in roles]
    admin_role = next((n for n in role_names if n and "Administrator" in n), None)

    client.modules_admin.get_or_create_module(
        module,
        label="pyfsr Wizard Demo",
        fields=[
            client.modules_admin.text_field("name", required=True),
            client.modules_admin.text_field("note"),
        ],
        display_template="{{ name }}",
        grant_to=[admin_role] if admin_role else None,
        add_to_nav=True,
        nav_title="Wizard Demo",
    )
    rec = client.records(module).create({"name": "demo-1", "note": "round-trip me"}, resolve_picklists=False)
    print(f"  created module {module!r} + record {rec.get('@id')}")

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / f"{module}.zip"

        # --- 1. EXPORT WIZARD --------------------------------------------- #
        print("\n=== 1. Export Wizard ===")
        tmpl = (
            ExportTemplate(f"{module} round-trip")
            .add_module(module)
            .add_record_set(module, query=Query(module=module).limit(100), limit=100)
        )
        client.export_config.create_template(tmpl)
        client.export_config.export_by_template_name(tmpl.name, output_path=str(zip_path), poll_interval=2)
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        print(f"  exported {zip_path.name} ({len(names)} entries)")
        for n in names:
            if n.endswith("mmd.json") or "/records/" in n:
                print(f"    {n}")

        # --- 2. TRUE DELETE (API discard + Postgres drop) ----------------- #
        print("\n=== 2. Delete (API + Postgres) ===")
        _full_delete(client, drop, module)
        assert client.modules_admin.get_published(module) is None, "module should be gone"
        print("  confirmed: module absent from the API")

        # --- 3. IMPORT WIZARD --------------------------------------------- #
        print("\n=== 3. Import Wizard ===")
        result = client.workflow_collections.import_export_zip(
            zip_path,
            create_modules=True,  # recreate the module from the export's mmd.json
            grant_modules_to=[admin_role] if admin_role else None,  # else 403 on records
            create_records=True,
        )
        print(f"  modules created: {result['modules_created']}")
        print(f"  records created: {len(result['records_created'])}")

    # --- 4. VERIFY -------------------------------------------------------- #
    print("\n=== 4. Verify ===")
    published = client.modules_admin.get_published(module)
    back = client.records(module).query(Query(module=module).eq("name", "demo-1").limit(1))
    members = back.members if hasattr(back, "members") else []
    ok = bool(published) and bool(members)
    print(f"  module published again: {bool(published)}")
    print(f"  record 'demo-1' present: {bool(members)}")

    if not args.keep:
        print("\n=== cleanup ===")
        _full_delete(client, drop, module)

    print(f"\n{'PASS' if ok else 'FAIL'} — export/import wizard round-trip")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
