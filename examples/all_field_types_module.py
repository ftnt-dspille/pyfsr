"""Create a module that exercises every field type supported by ModulesAdminAPI.

This is both a living example and a validation test: if the appliance publishes
the module cleanly, every field builder + the type/formType mapping is correct.
Running it against a real appliance surfaces any mismatch before it shows up
in customer code.

Field types covered:
  Scalar strings:  text, textarea, richtext, html, email, url, phone, domain,
                   filehash, ipv4, ipv6, password, file
  Numeric:         integer, decimal
  Temporal:        datetime  (stored as epoch-millis integer)
  Boolean:         checkbox
  Structured:      json, object
  Picklist:        single-select, multi-select
  Relationships:   lookup (many-to-one), manyToMany, oneToMany

Usage:
    python examples/all_field_types_module.py \\
        --host fortisoar.example.com --user csadmin --password changeme

    # Keep the module after the run (skip auto-delete):
    python examples/all_field_types_module.py ... --keep

    # Skip the publish step (cheaper; just validates staging creation):
    python examples/all_field_types_module.py ... --skip-publish

Environment variables:
    FSR_BASE_URL, FSR_USERNAME, FSR_PASSWORD
"""

from __future__ import annotations

import argparse
import os

from pyfsr import FortiSOAR
from pyfsr.api.modules_admin import ModulesAdminAPI as MA

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODULE_API = "pyfsr_all_field_types"
MODULE_LABEL = "PyFSR All Field Types"

# We create a tiny helper module so the manyToMany / oneToMany fields have a
# real target to point at. The oneToMany back-field must be a lookup on the
# target, so we add it there too.
HELPER_MODULE_API = "pyfsr_aft_helper"
HELPER_MODULE_LABEL = "PyFSR AFT Helper"

# A picklist that ships with every FSR appliance — safe to use as a reference.
EXISTING_PICKLIST = "Severity"


# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------


def _main_fields(ma: MA) -> list[dict]:
    """One field per supported widget type."""
    return [
        # --- string-family (all store 'string', differ only in UI widget) ---
        ma.text_field("fText", label="Text (single-line)", required=True),
        ma.text_field("fTextarea", label="Text (textarea)", area=True),
        ma.text_field("fRichtext", label="Text (richtext)", rich=True),
        ma.text_field("fHtml", label="Text (HTML)", html=True),
        ma.email_field("fEmail", label="Email"),
        ma.url_field("fUrl", label="URL"),
        ma.phone_field("fPhone", label="Phone"),
        ma.domain_field("fDomain", label="Domain"),
        ma.filehash_field("fFilehash", label="File Hash"),
        ma.ipv4_field("fIpv4", label="IPv4"),
        ma.ipv6_field("fIpv6", label="IPv6"),
        ma.password_field("fPassword", label="Password", encrypted=True),
        ma.file_field("fFile", label="File Attachment"),
        # --- numeric ---
        ma.integer_field("fInteger", label="Integer"),
        ma.decimal_field("fDecimal", label="Decimal"),
        # --- temporal ---
        ma.datetime_field("fDatetime", label="Date/Time"),
        # --- boolean ---
        ma.checkbox_field("fCheckbox", label="Checkbox"),
        # --- structured ---
        ma.json_field("fJson", label="JSON (editor widget)"),
        ma.object_field("fObject", label="Object (raw widget)"),
        # --- picklists ---
        ma.picklist_field("fPicklist", EXISTING_PICKLIST, label="Picklist (single)"),
        ma.picklist_field("fMultiPicklist", EXISTING_PICKLIST, multi=True, label="Picklist (multi-select)"),
        # --- relationships (target = HELPER_MODULE_API) ---
        ma.lookup_field("fLookup", HELPER_MODULE_API, label="Lookup (many-to-one)"),
        ma.relationship_field("fManyToMany", HELPER_MODULE_API, many=True, label="Relationship (manyToMany)"),
        ma.relationship_field(
            "fOneToMany",
            HELPER_MODULE_API,
            many=False,
            # the back-field on the helper module is 'backToMain'
            inversed_field="backToMain",
            label="Relationship (oneToMany)",
        ),
    ]


def _helper_fields(ma: MA) -> list[dict]:
    """Minimal helper module: just a name field + the back-lookup for oneToMany."""
    return [
        ma.text_field("name", label="Name", required=True),
        # required by the oneToMany on the main module
        ma.lookup_field("backToMain", MODULE_API, label="Back to Main"),
    ]


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------


def ensure_helper(fsr: FortiSOAR, ma: MA) -> None:
    if fsr.modules_admin.get_staging(HELPER_MODULE_API):
        print(f"helper module '{HELPER_MODULE_API}' already in staging — skipping")
        return
    print(f"creating helper module '{HELPER_MODULE_API}' ...")
    ma.create_module(
        HELPER_MODULE_API,
        label=HELPER_MODULE_LABEL,
        plural=f"{HELPER_MODULE_LABEL}s",
        fields=_helper_fields(ma),
    )
    print("  helper staging record created")


def ensure_main(fsr: FortiSOAR, ma: MA) -> None:
    if fsr.modules_admin.get_staging(MODULE_API):
        print(f"main module '{MODULE_API}' already in staging — skipping")
        return
    print(f"creating main module '{MODULE_API}' ({len(_main_fields(ma))} fields) ...")
    ma.create_module(
        MODULE_API,
        label=MODULE_LABEL,
        plural=f"{MODULE_LABEL}s",
        fields=_main_fields(ma),
        ownable=True,
        trackable=True,
        taggable=True,
        display_template="{{ fText }}",
    )
    print("  staging record created")


def validate_staging(fsr: FortiSOAR) -> None:
    """Read back the staging record and verify every field was created."""
    staging = fsr.modules_admin.get_staging(MODULE_API)
    if not staging:
        raise RuntimeError(f"staging record for '{MODULE_API}' not found after creation")

    created = {a["name"]: a for a in staging.get("attributes", [])}
    expected = {f["name"] for f in _main_fields(fsr.modules_admin)}

    missing = expected - created.keys()
    if missing:
        raise RuntimeError(f"staging is missing fields: {sorted(missing)}")

    print(f"\nstaging validation — {len(created)} attributes present:")
    for name, attr in sorted(created.items()):
        storage = attr.get("type", "?")
        widget = attr.get("formType", "?")
        enc = " [encrypted]" if attr.get("encrypted") else ""
        coll = " [collection]" if attr.get("collection") else ""
        req = " [required]" if (attr.get("validation") or {}).get("required") else ""
        print(f"  {name:22s}  type={storage:12s}  formType={widget:22s}{req}{enc}{coll}")


def publish_and_verify(fsr: FortiSOAR) -> None:
    print("\npublishing (appliance-wide, may take ~60 s) ...")
    result = fsr.modules_admin.publish()
    print(f"publish status: {result.get('status')}")

    if not fsr.modules_admin.is_published(MODULE_API):
        raise RuntimeError(f"'{MODULE_API}' not found in published model_metadatas after publish")
    print(f"'{MODULE_API}' confirmed published")


def teardown(fsr: FortiSOAR) -> None:
    for mod in (MODULE_API, HELPER_MODULE_API):
        staging = fsr.modules_admin.get_staging(mod)
        if staging:
            fsr.modules_admin.discard_staging_draft(mod)
            print(f"discarded staging module '{mod}'")
        else:
            print(f"staging module '{mod}' not found — nothing to delete")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("FSR_BASE_URL", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FSR_PORT", "443")))
    parser.add_argument("--user", default=os.environ.get("FSR_USERNAME", "csadmin"))
    parser.add_argument("--password", default=os.environ.get("FSR_PASSWORD"))
    parser.add_argument("--keep", action="store_true", help="Leave modules in place after the run")
    parser.add_argument(
        "--skip-publish",
        action="store_true",
        help="Skip the publish step (faster; staging validation only)",
    )
    args = parser.parse_args()

    if not args.password:
        raise SystemExit("set --password or FSR_PASSWORD")

    fsr = FortiSOAR(
        args.host,
        username=args.user,
        password=args.password,
        verify_ssl=False,
        suppress_insecure_warnings=True,
        port=args.port,
    )
    ma = fsr.modules_admin

    try:
        # helper must exist before main (oneToMany back-field needs it)
        ensure_helper(fsr, ma)
        ensure_main(fsr, ma)
        validate_staging(fsr)

        if not args.skip_publish:
            publish_and_verify(fsr)
        else:
            print("\n(skipping publish — staging validation only)")

        print("\nall field types created and validated successfully")

    finally:
        if not args.keep:
            print()
            teardown(fsr)
        else:
            print(f"\nmodules left in place: {MODULE_API}, {HELPER_MODULE_API}")


if __name__ == "__main__":
    main()
