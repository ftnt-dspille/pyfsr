"""Demonstrate solution pack install → status → uninstall lifecycle.

Usage:
    cd examples/
    python solution_pack_lifecycle.py [--pack NAME] [--version VER]

Defaults to 'xf-sample' 10.0.0 — a lightweight test pack that installs and
uninstalls cleanly.
"""

import argparse
import sys

import tomllib

sys.path.insert(0, "../src")
from pyfsr import FortiSOAR
from pyfsr.models import SolutionPackInstallResponse

parser = argparse.ArgumentParser()
parser.add_argument("--pack", default="xf-sample")
parser.add_argument("--version", default="10.0.0")
parser.add_argument("--no-uninstall", action="store_true")
args = parser.parse_args()

with open("config.toml", "rb") as f:
    config = tomllib.load(f)

fsr = config["fortisoar"]
client = FortiSOAR(
    base_url=fsr["base_url"],
    username=fsr.get("username", "csadmin"),
    password=fsr.get("password", "changeme"),
    verify_ssl=fsr.get("verify_ssl", True),
    suppress_insecure_warnings=True,
)

print(f"\n--- install {args.pack} v{args.version} ---")
resp = client.solution_packs.install(args.pack, args.version)
print(f"type        : {type(resp).__name__}")
if isinstance(resp, SolutionPackInstallResponse):
    print(f"pack uuid   : {resp.uuid}")
    print(f"pack name   : {resp.name}")
    print(f"job_id      : {resp.job_id}")
    if resp.job_id:
        print(f"\n--- poll install_status({resp.job_id}) ---")
        status = client.solution_packs.install_status(resp.job_id)
        print(f"status      : {status.status}")
        print(f"progress    : {status.progressPercent}%")
        if status.status and "complete" not in status.status.lower():
            print("\n--- wait_for_install (blocking) ---")
            final = client.solution_packs.wait_for_install(resp.job_id, interval=3, timeout=120)
            print(f"final status: {final.status}")
            print(f"error msg   : {final.errorMessage}")
else:
    print(f"status      : {resp}")

if not args.no_uninstall:
    print("\n--- verify installed ---")
    found = client.content_hub.find_installed_pack(args.pack, typed=True)
    if found:
        print(f"confirmed   : {found.name} v{found.version} uuid={found.uuid}")
        print(f"\n--- uninstall {args.pack} ---")
        client.solution_packs.uninstall(args.pack)
        print("uninstall   : OK")
        after = client.content_hub.find_installed_pack(args.pack)
        print(f"still there : {after is not None}")
    else:
        print("pack not found as installed — install may have failed or is async")
