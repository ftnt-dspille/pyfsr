#!/usr/bin/env python3
"""Create an alert and list alerts -- the pyfsr "hello world".

Shows both halves of the alerts surface:

  1. ``client.alerts.create(...)`` -- create an alert. Friendly picklist
     values (``severity="High"``) are resolved to the picklist IRIs
     FortiSOAR stores automatically.
  2. ``client.records("alerts").list(...)`` -- the modern listing surface:
     it unpacks the Hydra envelope and returns typed ``Alert`` records.
     (``client.alerts.list()`` returns the raw Hydra envelope instead.)

Picklists read the same way they write: severity and status come back as
``"High"`` / ``"New"``, not the picklist IRIs the API stores. pyfsr
translates both directions by default; pass ``resolve_picklists=False``
when you want the raw IRIs.

The client is built from ``config.toml`` in this directory (the
``[fortisoar]`` layout). Copy ``config.toml.example`` and fill in your
appliance -- the file looks like:

    # config.toml
    [fortisoar]
    base_url = "https://fortisoar.example.com"   # or an IP: https://X.X.X.X
    verify_ssl = false                           # lab self-signed certs

    [fortisoar.auth]
    type = "user_pass"                           # or "api_key"
    username = "csadmin"
    password = "<your-password>"
    # type = "api_key"
    # key = "your-api-key"

By default the alert created here is deleted at the end so the demo stays
re-runnable; pass ``--keep`` to leave it in the alert queue.

Live-verified on FortiSOAR 8.0.

Usage:
    python create_and_list_alerts.py
    python create_and_list_alerts.py --keep
    python create_and_list_alerts.py --config /path/to/config.toml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyfsr import FortiSOAR


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--config",
        default=str(Path(__file__).with_name("config.toml")),
        help="path to config.toml (default: examples/config.toml)",
    )
    ap.add_argument(
        "--keep",
        action="store_true",
        help="leave the created alert in the queue instead of deleting it",
    )
    args = ap.parse_args()

    client = FortiSOAR.from_config_file(args.config, suppress_insecure_warnings=True)
    print(f"connected: {client.base_url}")

    print("\n[1] create an alert -- friendly picklist values resolved automatically")
    alert = client.alerts.create(
        name="pyfsr create_and_list demo alert",
        description="Created by examples/create_and_list_alerts.py",
        severity="High",
        status="Open",  # alerts.status -> the AlertStatus picklist
        source="pyfsr",
    )
    uuid = alert.get("uuid") or str(alert["@id"]).rsplit("/", 1)[-1]
    print(f"    created {alert['name']!r}  severity={alert['severity']}  status={alert['status']}")
    print(f"    uuid={uuid}")

    print("\n[2] list alerts via the typed records surface")
    page = client.records("alerts").list(limit=10)
    print(f"    {page.total} alert(s) in total, showing {page.count}:")
    for row in page.members:
        print(f"    {row.uuid}  {row.severity or '-':8}  {row.status or '-':12}  {row.name}")

    found = next((row for row in page.members if row.uuid == uuid), None)
    print(f"\n[3] the created alert {'IS' if found else 'is NOT'} on page 1")
    if not found:
        print("    FAILED: created alert did not show up in the listing")
        return 1

    if args.keep:
        print(f"\nkept: delete it later with client.alerts.delete({uuid!r})")
        return 0

    print("\n[4] clean up")
    client.alerts.delete(uuid)
    print(f"    deleted {uuid}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
