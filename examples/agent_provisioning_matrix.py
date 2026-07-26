#!/usr/bin/env python3
"""Agent-provisioning matrix — the SOAR-side companion to a fleet of LXC agents.

This is the FortiSOAR/pyfsr half of the integration test driven by
``fortisoar/lab/agent-test/provision_agent.py`` + the Fabric Studio
``topologies/agent_matrix/`` topology (see that topology's header). The topology
stands up N Rocky LXCs, each of which self-provisions as a FortiSOAR **execution
agent** against a target SOAR using an API key. This script does the three things
pyfsr is uniquely good at, end to end:

  1. MINT the API key the agents authenticate with — ``client.api_keys.ensure_usable``
     (basic-auth in, plaintext key out; the two-step user+binding lifecycle in one
     call). This is the key you hand to provision_agent's ``--fsoc-api-key``.
  2. VERIFY registration — list agents, find the one the LXC registered, confirm it
     is alive over the secure-message bus (``client.agents.heartbeat``).
  3. Prove the ROUND TRIP — install a connector on the remote agent, bind a
     configuration to it, and ``execute`` an operation that actually runs *on the
     agent's host*. A Success here is the whole point: SOAR → agent → SOAR.

Works against any FortiSOAR the agent can reach (live-verified on 7.6.x and 8.0).

Usage:
    # mint a key to feed provision_agent (run BEFORE provisioning the LXCs)
    python agent_provisioning_matrix.py mint     --instance 159
    # after the LXC has provisioned, verify + exercise the agent
    python agent_provisioning_matrix.py verify   --instance 159 --agent agent-159-py39

Credentials come from a pyfsr instance registry entry or plain env vars
(FSR_BASE_URL / FSR_USERNAME / FSR_PASSWORD, or FSR_API_KEY). See config.toml.example.
"""

from __future__ import annotations

import argparse
import os
import sys

from pyfsr import FortiSOAR

# The role/team the minted key is bound to. Full App Permissions is the pairing
# provision_agent's agent-management calls need; scope down for least privilege.
KEY_ROLES = ["Full App Permissions"]
KEY_TEAM_FALLBACKS = ["SOC Team", "SOC - Security"]

# A trivial, side-effect-free op present on the utilities connector that
# provision_agent installs on every agent — used to prove the round trip.
PROBE_CONNECTOR = "cyops_utilities"
PROBE_OPERATION = "get_hash"  # hashes an inline string on the agent host
PROBE_PARAMS = {"input": "agent-roundtrip-probe", "algorithm": "sha256"}


def _client_basic(base_url: str, user: str, password: str) -> FortiSOAR:
    return FortiSOAR(base_url, username=user, password=password, verify_ssl=False)


def _resolve_env(instance: str) -> tuple[str, str, str]:
    """(base_url, user, password) from env — swap for your instance registry."""
    base = os.environ.get("FSR_BASE_URL")
    port = os.environ.get("FSR_PORT")
    if base and port and ":" not in base.split("//", 1)[-1]:
        base = f"{base}:{port}"
    user = os.environ.get("FSR_USERNAME", "csadmin")
    pw = os.environ.get("FSR_PASSWORD")
    if not (base and pw):
        sys.exit("set FSR_BASE_URL (+FSR_PORT) and FSR_PASSWORD, or wire your registry")
    return base, user, pw


def _pick_team(client: FortiSOAR) -> str:
    names = {t.get("name") for t in client.get("/api/3/teams?$limit=100").get("hydra:member", [])}
    for want in KEY_TEAM_FALLBACKS:
        if want in names:
            return want
    return sorted(names)[0]


def cmd_mint(args: argparse.Namespace) -> None:
    """Mint (or rotate) the API key provision_agent will authenticate with."""
    base, user, pw = _resolve_env(args.instance)
    client = _client_basic(base, user, pw)
    team = args.team or _pick_team(client)
    binding, plaintext = client.api_keys.ensure_usable(
        name=args.name,
        roles=KEY_ROLES,
        teams=[team],
    )
    bid = binding.get("@id") if hasattr(binding, "get") else getattr(binding, "id", binding)
    print(f"binding : {bid}")
    print(f"team    : {team}")
    print(f"API-KEY : {plaintext}")
    print("\nFeed this to provision_agent:  --fsoc-host <host> --fsoc-api-key <API-KEY>")


def cmd_verify(args: argparse.Namespace) -> None:
    """Confirm the agent registered, is alive, and can run an operation."""
    base, user, pw = _resolve_env(args.instance)
    client = _client_basic(base, user, pw)

    agents = client.agents.list()
    match = next((a for a in agents if getattr(a, "name", None) == args.agent), None)
    if match is None:
        print(f"agent {args.agent!r} not registered yet. Known: {[getattr(a, 'name', '?') for a in agents]}")
        sys.exit(1)
    agent_id = match.agentId
    print(f"agent   : {match.name}  (agentId={agent_id}, uuid={match.uuid})")

    hb = client.agents.heartbeat(agent_id)
    print(f"heartbeat: {hb.get('status') or hb}")

    # Ensure the probe connector is installed on the agent, then run the op there.
    print(f"install : {PROBE_CONNECTOR} on {match.name} ...")
    client.agents.install_connector(agent_id, name=PROBE_CONNECTOR, version=args.connector_version)
    for row in client.agents.connector_install_status(PROBE_CONNECTOR, args.connector_version, agent_id=agent_id):
        print(f"  status: {row.status}")

    # Bind a configuration to THIS agent, then execute against it — the op runs on
    # the agent host, not the appliance self-agent.
    cfg = client.connectors.create_configuration(
        PROBE_CONNECTOR,
        {},
        version=args.connector_version,
        name=f"{args.agent}-probe",
        agent=agent_id,
    )
    cfg_id = cfg.get("@id") if hasattr(cfg, "get") else cfg
    result = client.connectors.execute(
        PROBE_CONNECTOR, PROBE_OPERATION, config=f"{args.agent}-probe", params=PROBE_PARAMS
    )
    print(f"execute : {PROBE_OPERATION} -> ok={getattr(result, 'ok', None)}")
    print(f"  data  : {result.get('data')}")
    print(f"\nSOAR → agent → SOAR round trip: {'PASS' if getattr(result, 'ok', False) else 'FAIL'}  (config {cfg_id})")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mint", help="mint the API key for provision_agent")
    m.add_argument("--instance", default="159")
    m.add_argument("--name", default="agent-matrix-provisioner")
    m.add_argument("--team", default=None, help="team name (default: first SOC-ish team)")
    m.set_defaults(func=cmd_mint)

    v = sub.add_parser("verify", help="verify + exercise a registered agent")
    v.add_argument("--instance", default="159")
    v.add_argument("--agent", required=True, help="agent record name (e.g. agent-159-py39)")
    v.add_argument("--connector-version", default="3.7.1")
    v.set_defaults(func=cmd_verify)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
