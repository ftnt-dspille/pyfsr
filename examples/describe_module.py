"""Pretty-print a module's fields with type, required-ness, and conditions.

``client.modules_admin.format_module(module)`` renders an aligned table showing
each field's storage type, display form, REQUIRED (incl. "Required by
condition"), VISIBLE (incl. "Visible by condition"), and friendly title — the
full picture the module editor shows, not just the bool collapse that
``client.modules.format_module`` gives.

The offline tour builds a synthetic record and calls ``format_module_record``
(the pure helper behind ``format_module``) so you can see every condition shape
with no appliance. The live tour calls ``format_module`` against a real box.

Usage:
    python examples/describe_module.py                        # offline tour only
    FSR_BASE_URL=https://box:13006 FSR_USERNAME=csadmin FSR_PASSWORD=... \\
        python examples/describe_module.py                    # + live, all modules
    ... python examples/describe_module.py --module alerts --module incident
    ... python examples/describe_module.py --staging          # read drafts, not published

Example output (from the offline tour — a synthetic module exercising every
required/visibility shape):

    Module: Demo Incidents  (type=demo_incidents, fields=8)

    NAME            TYPE       FORM                 REQUIRED                  VISIBLE                 TITLE
    --------------  ---------  -------------------  ------------------------  ----------------------  --------------
    name            string     text                 yes                                               Name
    description     string     richtext                                                               Description
    severity        picklists  picklist             yes                                               Severity
    status          picklists  picklist                                       no                      Status
    closed_reason   string     text                 cond: status eq 'Closed'  cond: status ne 'Open'  Closed Reason
    assigned_to     users      lookup                                                                 Assigned To
    tags            picklists  multiselectpicklist                            cond: status ne 'Open'  Tags
    artifact_count  integer    integer                                                                Artifact Count

Only the non-default values show in REQUIRED / VISIBLE: a bare ``yes`` means
"required", ``no`` in the VISIBLE column means "hidden", and ``cond: ...`` is
the "Required/Visible by condition" filter (``status eq 'Closed'`` etc.).
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from pyfsr import FortiSOAR
from pyfsr.api.modules_admin import ModulesAdminAPI

# ---------------------------------------------------------------------------
# Synthetic module for the offline tour (no appliance needed)
# ---------------------------------------------------------------------------


def _demo_record() -> dict[str, Any]:
    """A synthetic published-module record exercising every required/visibility shape.

    Mirrors the ``get_published`` return shape (``attributes`` list with
    ``validation.required`` and top-level ``visibility``). Field types cover the
    common widgets; the interesting rows are ``status`` (hidden),
    ``closed_reason`` (required-by-condition + visible-by-condition) and
    ``tags`` (visible-by-condition).
    """

    def attr(name, db_type, form_type, *, required=False, visibility=True, title=None):
        return {
            "name": name,
            "type": db_type,
            "formType": form_type,
            "displayName": f"{{{{ {name} }}}}",
            "descriptions": {"singular": title or name.replace("_", " ").title()},
            "validation": {"required": required, "minlength": 0, "maxlength": 10485760},
            "visibility": visibility,
        }

    closed_when_status_closed = {
        "logic": "AND",
        "filters": [{"field": "status", "operator": "eq", "value": "Closed"}],
    }
    visible_when_not_open = {
        "logic": "AND",
        "filters": [{"field": "status", "operator": "ne", "value": "Open"}],
    }
    return {
        "type": "demo_incidents",
        "module": "demo_incidents",
        "uuid": "00000000-0000-4000-8000-000000000001",
        "displayName": "{{ name }}",
        "descriptions": {"singular": "Demo Incidents", "plural": "Demo Incidents"},
        "attributes": [
            attr("name", "string", "text", required=True, title="Name"),
            attr("description", "string", "richtext", title="Description"),
            attr("severity", "picklists", "picklist", required=True, title="Severity"),
            attr("status", "picklists", "picklist", visibility=False, title="Status"),
            attr(
                "closed_reason",
                "string",
                "text",
                required=closed_when_status_closed,
                visibility=visible_when_not_open,
                title="Closed Reason",
            ),
            attr("assigned_to", "users", "lookup", title="Assigned To"),
            attr(
                "tags",
                "picklists",
                "multiselectpicklist",
                visibility=visible_when_not_open,
                title="Tags",
            ),
            attr("artifact_count", "integer", "integer", title="Artifact Count"),
        ],
    }


def offline_tour() -> None:
    """Print the synthetic module — every required/visibility shape, no creds."""
    print("=" * 78)
    print("OFFLINE — synthetic module (no appliance needed)")
    print("=" * 78)
    print()
    # format_module_record is the pure helper behind format_module — reusable
    # for any record dict (synthetic, captured, or from get_published/get_staging).
    print(ModulesAdminAPI.format_module_record(_demo_record()))
    print()
    print("(status is hidden; closed_reason is required-by-condition AND")
    print(" visible-by-condition; tags is visible-by-condition only.)")


# ---------------------------------------------------------------------------
# Live tour — read published (or staging) metadata from a real appliance
# ---------------------------------------------------------------------------


def _filter_modules(all_modules: list[dict[str, Any]], substrings: list[str]) -> list[dict[str, Any]]:
    """Match ``--module`` substrings against type/label/plural (case-insensitive)."""
    subs = [s.strip().lower() for s in substrings if s.strip()]
    if not subs:
        return all_modules
    out = []
    for m in all_modules:
        hay = " ".join(str(m.get(k, "")).lower() for k in ("type", "label", "plural"))
        if any(s in hay for s in subs):
            out.append(m)
    return out


def live_tour(modules: list[str], *, staging: bool = False) -> None:
    """Read each module's metadata from a real box and pretty-print it."""
    base = os.environ.get("FSR_BASE_URL")
    user = os.environ.get("FSR_USERNAME")
    pw = os.environ.get("FSR_PASSWORD")
    if not (base and user and pw):
        print("\n(skipping live tour — set FSR_BASE_URL/FSR_USERNAME/FSR_PASSWORD)")
        return

    verify = os.environ.get("FSR_VERIFY_SSL", "false").lower() in ("1", "true", "yes")
    client = FortiSOAR(base, username=user, password=pw, verify_ssl=verify)
    admin = client.modules_admin

    print("\n" + "=" * 78)
    print(f"LIVE — {client.base_url} (version {client.version()})  [{'staging' if staging else 'published'}]")
    print("=" * 78)

    all_mods = client.modules.list()
    selected = _filter_modules(all_mods, modules)
    if not selected:
        print(f"\nno modules matched {modules!r}; available: {[m['type'] for m in all_mods[:20]]} ...")
        return

    print(f"\n{len(selected)} module(s) selected:")
    for m in selected:
        # format_module fetches the record and renders it in one call.
        print()
        print(admin.format_module(m["type"], staging=staging))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        metavar="SUBSTRING",
        help="limit the live tour to modules whose type/label/plural contains "
        "this substring (repeatable); default is every module",
    )
    parser.add_argument(
        "--staging",
        action="store_true",
        help="read staging (draft) metadata instead of published",
    )
    args = parser.parse_args()

    offline_tour()
    live_tour(args.module, staging=args.staging)


if __name__ == "__main__":
    main()
