"""Safely pin a connector to a specific version, preserving its configurations.

Demonstrates the config-export → version-change → config-restore round-trip that
makes a connector **downgrade** non-destructive. Uninstalling a connector
cascade-deletes its saved configurations (credentials), so the safe pattern is:

  1. export the connector's configs to a backup ``.zip`` (secrets travel
     encrypted with the appliance key, so a same-box restore is lossless),
  2. install the target version in place (an in-place install — upgrade *or*
     downgrade — preserves configs on its own),
  3. if the version change dropped any configs, re-import them from the backup.
     The import is an upsert keyed by ``config_id``, so restored configs keep
     their original UUIDs and any playbook step that references them keeps working.

``client.connectors.ensure_version()`` orchestrates all of that. This script
shows both the one-call form and the underlying pieces
(``export_config.export_connector`` + ``import_config.import_file``).

Nothing here installs anything unless you set a real ``TARGET_VERSION`` *and*
flip ``DO_WRITES`` — by default it only exports a backup (read-only) so you can
point it at a live appliance safely.

Configure via env (or edit the constants below):
  FSR_BASE_URL, FSR_USERNAME, FSR_PASSWORD   -> FortiSOAR appliance
  CONNECTOR_NAME                             -> the connector to pin
  TARGET_VERSION                             -> version to ensure (blank -> just back up)
  BACKUP_DIR                                 -> where to write the backup .zip
"""

from __future__ import annotations

import os

from pyfsr import FortiSOAR

FSR_BASE_URL = os.environ.get("FSR_BASE_URL", "fortisoar.example.com")
FSR_USERNAME = os.environ.get("FSR_USERNAME", "csadmin")
FSR_PASSWORD = os.environ.get("FSR_PASSWORD", "<redacted>")

CONNECTOR_NAME = os.environ.get("CONNECTOR_NAME", "code-snippet")
TARGET_VERSION = os.environ.get("TARGET_VERSION", "")  # blank -> only export a backup
BACKUP_DIR = os.environ.get("BACKUP_DIR", ".")

# Flip to True to actually install TARGET_VERSION (and, if needed, restore
# configs). Left False so the example only takes a backup.
DO_WRITES = os.environ.get("DO_WRITES", "").lower() in ("1", "true", "yes")


def _show_configs(conn, name: str) -> None:
    cfgs = conn.configurations(name)
    if not cfgs:
        print(f"  (no configurations on {name})")
        return
    for c in cfgs:
        flag = " [default]" if c.get("default") else ""
        print(f"  - {c['name']}{flag}  config_id={c['config_id']}")


def main() -> None:
    # New explicit-kwargs auth: username/password for a login, or token=<api-key>.
    client = FortiSOAR(
        FSR_BASE_URL,
        username=FSR_USERNAME,
        password=FSR_PASSWORD,
        verify_ssl=False,
        suppress_insecure_warnings=True,
    )
    conn = client.connectors

    current = conn.resolve_version(CONNECTOR_NAME)
    if current is None:
        raise SystemExit(f"{CONNECTOR_NAME!r} is not installed on this appliance")

    print(f"== {CONNECTOR_NAME} is at v{current} ==")
    print("current configurations:")
    _show_configs(conn, CONNECTOR_NAME)

    # ---- always: take a backup of the configs --------------------------------
    backup = client.export_config.export_connector(
        CONNECTOR_NAME,
        output_path=os.path.join(BACKUP_DIR, f"{CONNECTOR_NAME}-{current}-backup.zip"),
    )
    print(f"\nbacked up configs -> {backup}")

    if not TARGET_VERSION:
        print("\nTARGET_VERSION not set — backup only, nothing changed.")
        print(f"To restore later:  client.import_config.import_file({backup!r}, wait=True)")
        return

    if not DO_WRITES:
        print(f"\nWould ensure {CONNECTOR_NAME} == v{TARGET_VERSION} (set DO_WRITES=1 to actually install).")
        return

    # ---- one-call orchestration ----------------------------------------------
    # Backs up, installs the target in place, verifies, and re-imports configs
    # only if the version change dropped them. allow_uninstall_fallback stays
    # off so a failed in-place change never wipes configs behind your back.
    print(f"\nensuring {CONNECTOR_NAME} == v{TARGET_VERSION} ...")
    result = conn.ensure_version(
        CONNECTOR_NAME,
        TARGET_VERSION,
        backup_dir=BACKUP_DIR,
        allow_uninstall_fallback=False,
    )
    print("result:", result)

    print("\nconfigurations after:")
    conn.clear_cache()
    _show_configs(conn, CONNECTOR_NAME)


if __name__ == "__main__":
    main()
