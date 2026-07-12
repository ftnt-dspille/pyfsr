"""Round-trip a record through config export and import.

Shows both halves of the Export Wizard from the SDK:

  1. ``client.export_config.export_record_data`` — build a throwaway export
     template with one *filtered* record set and download the ``.zip``. A record
     set only emits rows when its query carries a ``limit`` (the engine's export
     trigger); the SDK injects one for you.
  2. ``client.import_config.import_file`` — re-apply that ``.zip`` end to end
     (upload -> job -> options -> trigger -> wait), riding through any migrate-cycle
     5xx the way a publish does.

To prove the import actually *lands* the data (rather than no-opping over a row
that is still there), the demo deletes the record between export and import, then
confirms it comes back. It only ever touches a record it creates, and cleans up
after itself, so the box is left as it was found.

Usage:
    cd examples/
    python export_import_records.py [--module alerts] [--keep-zip]

Defaults to the ``alerts`` module — a plain record module that creates and
deletes cheaply. ``config.toml`` supplies the connection ([fortisoar] block).
"""

import argparse
import os
import sys

import tomllib

sys.path.insert(0, "../src")
from pyfsr import FortiSOAR, Query

parser = argparse.ArgumentParser()
parser.add_argument("--module", default="alerts", help="record module to round-trip")
parser.add_argument("--keep-zip", action="store_true", help="don't delete the export .zip")
args = parser.parse_args()

with open("config.toml", "rb") as f:
    fsr = tomllib.load(f)["fortisoar"]

client = FortiSOAR(
    base_url=fsr["base_url"],
    username=fsr.get("username", "csadmin"),
    password=fsr.get("password", "changeme"),
    verify_ssl=fsr.get("verify_ssl", True),
    suppress_insecure_warnings=True,
)

records = client.records(args.module)
marker = f"pyfsr_demo_{os.getpid()}"  # unique so the filter isolates our record
zip_path = os.path.join(os.getcwd(), f"{args.module}_roundtrip.zip")


def _matching():
    return list(records.query(Query(module=args.module).eq("name", marker)))


print(f"\n--- create throwaway {args.module} record ({marker!r}) ---")
created = records.create({"name": marker}, raw=True)
uuid = created["uuid"]
print(f"uuid        : {uuid}")

try:
    print("\n--- export it (filtered record set -> .zip) ---")
    client.export_config.export_record_data(
        args.module,
        query=Query(module=args.module).eq("name", marker),
        output_path=zip_path,
    )
    print(f"archive     : {zip_path} ({os.path.getsize(zip_path)} bytes)")

    print("\n--- delete it (so the import has something to restore) ---")
    records.delete(uuid, hard=True)
    print(f"present now  : {bool(_matching())}")

    print("\n--- import the archive back (full lifecycle, blocking) ---")
    result = client.import_config.import_file(zip_path, wait=True)
    print(f"status      : {result.status}")

    restored = _matching()
    print(f"\nrestored    : {bool(restored)}" + (f" (uuid={restored[0]['uuid']})" if restored else ""))
finally:
    print("\n--- clean up ---")
    for rec in _matching():
        records.delete(rec["uuid"], hard=True)
    if not args.keep_zip and os.path.exists(zip_path):
        os.remove(zip_path)
    print("done")
