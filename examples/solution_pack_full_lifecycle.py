"""Author, export, and reinstall a solution pack — the full lifecycle.

This goes beyond ``solution_pack_lifecycle.py`` (which installs an existing pack
from Content Hub): it *authors* a pack from selected content, publishes it,
exports it to a ``.zip``, uninstalls it, and reinstalls it from that file.

Stages
    1. CREATE  — build a pack from content + metadata, POST /api/3/solutionpacks
    2. EXPORT  — download the published pack as a .zip (by name)
    3. UNINSTALL
    4. UPLOAD  — reinstall the pack from the exported .zip

Usage:
    cd examples/
    python solution_pack_full_lifecycle.py [--name API_NAME] [--keep]

Live-verified end to end on 8.0.0. A pack import runs a schema migrate that
briefly restarts the API, so ``install_from_file(wait=True)`` tolerates a
transient 503 while it polls.
"""

import argparse
import sys

import tomllib

sys.path.insert(0, "../src")
from pyfsr import FortiSOAR
from pyfsr.api.export_config import SolutionPackBuilder

parser = argparse.ArgumentParser()
parser.add_argument("--name", default="pyfsr-demo-pack", help="pack API identifier")
parser.add_argument("--keep", action="store_true", help="leave the pack installed at the end")
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

# --------------------------------------------------------------------------- #
# 1. CREATE — author a pack from content selections + pack metadata
# --------------------------------------------------------------------------- #
print(f"\n--- create pack {args.name!r} ---")
pack = (
    SolutionPackBuilder("Pyfsr Demo Pack", name=args.name, version="1.0.0", description="Authored by pyfsr")
    .add_module("alerts")  # ship the alerts module schema
    .post_install_widget("AI Assistant", "5.0.0", auto_launch=False)  # post-install action
    .tags("pyfsr-demo")
)
created = client.solution_packs.create(pack, publish=True)
print(f"created     : {created.name} v{created.version} uuid={created.uuid} installed={created.installed}")

# --------------------------------------------------------------------------- #
# 2. EXPORT — download the published pack as a .zip (server ties the export to
#    the pack via its export template; no pack uuid needed).
# --------------------------------------------------------------------------- #
print("\n--- export pack ---")
zip_path = client.solution_packs.export_pack(args.name, output_path=f"{args.name}.zip")
print(f"exported    : {zip_path}")

# --------------------------------------------------------------------------- #
# 3. UNINSTALL
# --------------------------------------------------------------------------- #
print("\n--- uninstall ---")
client.solution_packs.uninstall(args.name)
print(f"still there : {client.content_hub.find_installed_pack(args.name) is not None}")

# --------------------------------------------------------------------------- #
# 4. UPLOAD — reinstall from the exported .zip (blocks through the import)
# --------------------------------------------------------------------------- #
print("\n--- install from file ---")
final = client.solution_packs.install_from_file(zip_path, replace=True, wait=True, interval=4, timeout=300)
print(f"final status: {final.status}")
reinstalled = client.content_hub.find_installed_pack(args.name)
print(f"reinstalled : {reinstalled is not None}")

if not args.keep:
    print("\n--- cleanup ---")
    client.solution_packs.uninstall(args.name)
    print("uninstalled : OK")
