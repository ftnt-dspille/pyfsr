#!/usr/bin/env python
"""Deploy the reconcile-and-report asset->issues modules (idempotent).

Creates the two FortiSOAR modules the archetype pilot writes to:

  recon_assets  (parent) -- one record per reconciled serial.
      natural key / uniqueConstraint: [join_key]
  recon_issues  (child)   -- one record per finding, linked to its asset.
      natural key / uniqueConstraint: [issue_key]  (= join_key + "|" + mismatch_type)
      `asset` lookup -> recon_assets

The playbook upserts by these natural keys (``/api/3/upsert/<module>`` + ``sourceId``
+ ``operation: Overwrite`` -- wired via ``is_upsert`` in the pilot YAML), so the
module ``uniqueConstraint`` is the DB backstop that makes a re-run *update* the
existing record instead of appending a duplicate.

What this script does (all idempotent via :meth:`get_or_create_module`):

  1. create ``recon_assets`` + publish  (parent first: the issues lookup targets it)
  2. create ``recon_issues`` + publish  (with the ``asset`` lookup -> recon_assets)
  3. resolve the ``MismatchType`` / ``ReconStatus`` picklist option IRIs and write
     them to ``scripts/_recon_picklists.json`` for the playbook compile to substitute
     (the pilot YAMLs ship placeholder IRIs with RE-RESOLVE comments).
  4. assert both modules are live with ``uniqueConstraint`` set + nav entries present.

Each module is created with:
  - ``record_uniqueness`` -> the platform ``uniqueConstraint`` (built at publish)
  - ``add_to_nav=True``    -> a nav-bar entry (gated by read perm), deferred to publish
  - ``grant_to``           -> full CRUD on the module for the named roles (deferred)

Env: BASE_URL (required), FSR_USERNAME (default csadmin), FSR_PASSWORD (required),
FSR_VERIFY_SSL (default false for self-signed appliance certs). A local .env file
(gitignored) is the easy way::

    set -a; . ./.env.testing; set +a
    .venv/bin/python scripts/deploy_recon_modules.py

Picklist step 3 now CREATES the lists + options if absent (idempotent) via the
new PicklistsAPI write helpers (``get_or_create_picklist`` / ``add_option``);
it then resolves every option label to its IRI for the playbook compile.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Run from the repo root without installing pyfsr.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pyfsr import FortiSOAR  # noqa: E402
from pyfsr.exceptions import FortiSOARException  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PICKLIST_OUT = REPO / "scripts" / "_recon_picklists.json"

# The picklist option labels the playbook needs (friendly values -> IRIs).
# MismatchType options must match the MT map in the pilot YAMLs' Reconcile step.
MISMATCH_TYPE_OPTIONS = ["missing-in-A", "missing-in-B", "license-expiring", "field-mismatch"]
RECON_STATUS_OPTIONS = ["Open", "In Progress", "Resolved", "False Positive"]

GRANT_ROLES = ["Full App Permissions"]


def _env(name: str, *, default: str | None = None, required: bool = True) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        if default is not None:
            return default
        if required:
            print(f"{name} is required (set it in your .env file)", file=sys.stderr)
            raise SystemExit(2)
    return v


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() not in {"0", "false", "no", "off", ""}


def _asset_fields(admin) -> list:
    """recon_assets fields: join_key (unique), name, source_a, source_b, last_reconciled_at."""
    return [
        admin.text_field("join_key", label="Join Key", required=True),
        admin.text_field("name", label="Name"),
        admin.text_field("source_a", label="Source A State"),
        admin.text_field("source_b", label="Source B State"),
        admin.datetime_field("last_reconciled_at", label="Last Reconciled At"),
    ]


def _issue_fields(admin) -> list:
    """recon_issues fields: issue_key (unique), asset (lookup->recon_assets), join_key,
    mismatch_type + status (picklists), source/target, reported_at, details."""
    return [
        admin.text_field("issue_key", label="Issue Key", required=True),
        # lookup (many-to-one) -> recon_assets. One-directional, no reverse field.
        admin.lookup_field("asset", "recon_assets", label="Asset"),
        admin.text_field("join_key", label="Join Key", required=True),
        admin.picklist_field("mismatch_type", "MismatchType", label="Mismatch Type", required=True),
        admin.picklist_field("status", "ReconStatus", label="Status", required=True),
        admin.text_field("source_system", label="Source System"),
        admin.text_field("target_system", label="Target System"),
        admin.text_field("source_id", label="Source ID"),
        admin.text_field("target_id", label="Target ID"),
        admin.datetime_field("reported_at", label="Reported At"),
        admin.text_field("details", label="Details", area=True),
    ]


def _create_module(client: FortiSOAR, module: str, *, fields, record_uniqueness, nav_title):
    admin = client.modules_admin
    meta, created = admin.get_or_create_module(
        module,
        fields=fields,
        label=nav_title,
        record_uniqueness=record_uniqueness,
        add_to_nav=True,
        nav_title=nav_title,
        grant_to=GRANT_ROLES,
    )
    flag = "created" if created else "exists (unchanged)"
    print(f"  [{module}] {flag}")
    return meta, created


def _wait_for_cache(client: FortiSOAR, *, tries: int = 40, delay: float = 5.0) -> None:
    """Block until the post-publish cache-clear / language-support generation settles.

    After an appliance-wide publish the API returns 503 ("Clearing Cache" / "Generating
    Language Support Files") for several seconds; reading modules during that window
    raises. Poll the published-modules read until it succeeds.
    """
    import time

    print("  waiting for cache to settle ...", end="", flush=True)
    for _ in range(tries):
        try:
            client.modules_admin.get_published("__probe__")  # None is fine; we just need a 2xx
            break
        except FortiSOARException:
            print(".", end="", flush=True)
            time.sleep(delay)
    print(" ok")


def _resolve_picklists(client: FortiSOAR) -> dict:
    """Ensure the MismatchType + ReconStatus picklists + their options exist, then
    return ``{listName: {label: iri, ...}}``.

    Idempotent: a list/option already present is reused (not recreated). Missing
    lists and missing options are created via the new PicklistsAPI write helpers
    (``get_or_create_picklist`` / ``add_option``), so the deploy is now fully
    end-to-end — no manual UI step.
    """
    out: dict[str, dict[str, str]] = {}
    want = {"MismatchType": MISMATCH_TYPE_OPTIONS, "ReconStatus": RECON_STATUS_OPTIONS}
    for name, labels in want.items():
        pn, created = client.picklists.get_or_create_picklist(name, system=False)
        flag = "created" if created else "exists"
        existing = {it.itemValue for it in pn.items}
        added: list[str] = []
        for idx, label in enumerate(labels):
            if label in existing:
                continue
            client.picklists.add_option(pn.iri or pn.uuid, label, order=idx)
            added.append(label)
        sub = "" if created else (f", +{len(added)} options" if added else "")
        print(f"  [{name}] {flag}{sub}")
        # resolve to IRIs (re-warmed after the writes) for the playbook compile
        client.picklists.clear_cache()
        out[name] = {label: iri for label in labels if (iri := client.picklists.resolve(label, picklist=name))}
    return out


def main() -> int:
    base_url = _env("BASE_URL")
    username = _env("FSR_USERNAME", default="csadmin")
    password = _env("FSR_PASSWORD")
    verify = _bool("FSR_VERIFY_SSL", False)

    client = FortiSOAR(base_url, username=username, password=password, verify_ssl=verify)
    print(f"connected to {client.base_url} (version {client.version()})\n")
    admin = client.modules_admin

    # 1. recon_assets (parent) -- create + publish first (issues lookup targets it).
    # get_or_create_module(publish=True) publishes on creation; when the module
    # already exists it returns unchanged (no publish, so no cache-clear window).
    print("recon_assets (parent, uniqueConstraint=[join_key]) ...")
    _, created_assets = _create_module(
        client,
        "recon_assets",
        fields=_asset_fields(admin),
        record_uniqueness=["join_key"],
        nav_title="Reconciliation Assets",
    )
    if created_assets:
        _wait_for_cache(client)
    print()

    # 2. recon_issues (child) -- create + publish (asset lookup -> recon_assets).
    print("recon_issues (child, uniqueConstraint=[issue_key], asset->recon_assets) ...")
    _, created_issues = _create_module(
        client,
        "recon_issues",
        fields=_issue_fields(admin),
        record_uniqueness=["issue_key"],
        nav_title="Reconciliation Issues",
    )
    if created_issues:
        _wait_for_cache(client)
    print()

    # 3. resolve picklist option IRIs -> JSON for the playbook compile to substitute.
    print("resolving picklist option IRIs ...")
    picklists = _resolve_picklists(client)
    PICKLIST_OUT.write_text(json.dumps(picklists, indent=2), encoding="utf-8")
    print(f"  wrote {PICKLIST_OUT.relative_to(REPO)} ({sum(len(v) for v in picklists.values())} IRIs)\n")

    # 4. assert modules live + uniqueConstraint + nav.
    print("assertions:")
    ok = True
    for module, key in (("recon_assets", "join_key"), ("recon_issues", "issue_key")):
        meta = admin.get_published(module)
        if not meta:
            print(f"  [FAIL] {module} not found in published modules")
            ok = False
            continue
        uc = meta.get("uniqueConstraint") or []
        cols = [c for entry in uc for c in (entry.get(f"{module}_unique", {}).get("columns", []))]
        nav = client.app_config.find_navigation_item(module=module)
        u_ok = key in cols
        n_ok = nav is not None
        status = "OK" if u_ok and n_ok else "FAIL"
        nav_state = "present" if n_ok else "MISSING"
        print(f"  [{status}] {module}: uniqueConstraint={cols!r} nav={nav_state}")
        ok = ok and u_ok and n_ok

    if ok:
        print("\nmodules deployed. Next: substitute _recon_picklists.json IRIs into the")
        print("pilot YAML (placeholder picklist UUIDs marked RE-RESOLVE), then compile + push.")
        return 0
    print("\none or more assertions failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
