"""Full connector lifecycle through ``client.connectors`` and ``client.agents``.

Walks the whole surface pyfsr wraps over FortiSOAR's ``/api/integration`` API —
including the appliance-level uninstall, the operations-discovery endpoint, the
dedicated configuration listing, the Connector Studio dev workspace, and the
per-agent install/upgrade/uninstall + heartbeat calls.

Nothing here is destructive unless you flip the ``DO_WRITES`` flag — by default
it only *reads* (list, discover, healthcheck, heartbeat) so you can point it at
a live appliance safely.

Configure via env (or edit the constants below):
  FSR_BASE_URL, FSR_USERNAME, FSR_PASSWORD   -> FortiSOAR appliance
  CONNECTOR_NAME, CONNECTOR_VERSION          -> the connector to exercise
  AGENT_ID                                   -> a remote agent hash (optional)
"""

from __future__ import annotations

import os

from pyfsr import FortiSOAR

FSR_BASE_URL = os.environ.get("FSR_BASE_URL", "fortisoar.example.com:13002")
FSR_USERNAME = os.environ.get("FSR_USERNAME", "csadmin")
FSR_PASSWORD = os.environ.get("FSR_PASSWORD", "changeme")

CONNECTOR_NAME = os.environ.get("CONNECTOR_NAME", "cyops_utilities")
CONNECTOR_VERSION = os.environ.get("CONNECTOR_VERSION", "")  # blank -> resolve installed
AGENT_ID = os.environ.get("AGENT_ID", "")  # a remote agent hash, optional

# Flip to True to exercise the write paths (configure / dev publish / agent
# install + upgrade + uninstall). Left False so the example is read-only.
DO_WRITES = os.environ.get("DO_WRITES", "").lower() in ("1", "true", "yes")


def main() -> None:
    client = FortiSOAR(FSR_BASE_URL, auth=(FSR_USERNAME, FSR_PASSWORD), verify_ssl=False)
    conn = client.connectors

    # ---- discovery -----------------------------------------------------------
    print("== installed + configured connectors ==")
    for c in conn.list_configured():
        cfgs = ", ".join(f"{x['name']}({'default' if x['default'] else '-'})" for x in c["configurations"]) or "(none)"
        print(f"  {c['name']:30} v{c['version']:8} id={c['id']}  configs: {cfgs}")

    version = CONNECTOR_VERSION or conn.resolve_version(CONNECTOR_NAME)
    if not version:
        print(f"\n{CONNECTOR_NAME!r} not installed; set CONNECTOR_VERSION to inspect it.")
        return

    # ---- operations discovery (spec-canonical, by integer id) ----------------
    print(f"\n== operations for {CONNECTOR_NAME} v{version} ==")
    detail = conn.connector_detail(CONNECTOR_NAME)
    for op in detail.get("operations", [])[:10]:
        required = [p["name"] for p in op.get("parameters", []) if p.get("required")]
        print(f"  {op['operation']:30} required params: {required or '—'}")

    # ---- the dedicated, filterable configuration listing ---------------------
    print(f"\n== saved configurations for {CONNECTOR_NAME} ==")
    for cfg in conn.list_configurations(name=CONNECTOR_NAME, active=True):
        print(f"  config_id={cfg.get('config_id')}  agent={cfg.get('agent')}")

    # ---- health --------------------------------------------------------------
    print("\n== healthcheck ==")
    print(" ", conn.healthcheck(CONNECTOR_NAME, version=version))

    # ---- Connector Studio dev workspace --------------------------------------
    print("\n== connector studio dev workspace ==")
    dev = conn.dev_list()
    print(f"  {len(dev)} connector(s) checked out for editing")
    for entity in dev[:5]:
        print(f"  {entity.get('name')}  id={entity.get('id')}")

    # ---- agent-side state ----------------------------------------------------
    if AGENT_ID:
        print(f"\n== agent {AGENT_ID} ==")
        print("  heartbeat:", client.agents.heartbeat(AGENT_ID).get("status"))
        rows = client.agents.connector_install_status(CONNECTOR_NAME, version, agent_id=AGENT_ID)
        print(f"  {CONNECTOR_NAME} install rows: {rows}")

    if not DO_WRITES:
        print("\n(read-only run — set DO_WRITES=1 to exercise the write paths)")
        return

    # ---- write paths (guarded) ----------------------------------------------
    # 1) edit a dev-workspace connector, save a file, publish it live.
    if dev:
        entity_id = dev[0]["id"]
        conn.dev_edit(entity_id)
        info = conn.dev_read_file(entity_id, dev[0].get("dev_path", ""))
        print("\n  read dev file:", str(info)[:80])
        # conn.dev_write_file(entity_id, {...})  # the editor's file object
        conn.dev_publish(entity_id, replace=True)
        print("  published dev workspace ->", entity_id)

    # 2) push the connector onto a remote agent, upgrade it, then remove it.
    if AGENT_ID:
        client.agents.install_connector(AGENT_ID, name=CONNECTOR_NAME, version=version)
        client.agents.upgrade_connector(AGENT_ID, name=CONNECTOR_NAME, version=version)
        client.agents.uninstall_connector(AGENT_ID, name=CONNECTOR_NAME, version=version)
        print(f"  install/upgrade/uninstall cycle done on agent {AGENT_ID}")

    # 3) uninstall the connector from the appliance itself.
    # conn.uninstall(CONNECTOR_NAME)  # uncomment to remove from the appliance


if __name__ == "__main__":
    main()
