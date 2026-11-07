#!/usr/bin/env python3
"""Agent x connector-config matrix: does every playbook step land where YAML says?

A connector configuration can live on the hub or on any remote agent, and two
locations may hold a config of the same NAME. Playbook YAML names the config
(``config: Lab AD``) and, optionally, the agent (``on_remote: branch-agent``);
the compiler turns those into the wire ``agent`` (hex agentId) and the config
UUID of THAT location. This example proves it end to end against a live box:

  1. DISCOVER every configuration of ``--connector`` and where it lives.
  2. DERIVE the expected outcome of every combination:
       * ``on_remote: X`` + a config on X           -> X's config
       * ``on_remote: X`` + a config only elsewhere -> compile error naming where it is
       * ``on_remote: X`` + no config               -> X's default config
       * ``config: N`` alone, N on one location      -> that location
       * ``config: N`` alone, N on the hub + agents -> the hub's (with a warning)
       * ``config: N`` alone, N on several agents    -> compile error (set on_remote)
  3. COMPILE each case with ``client.workflow_collections.compile_yaml`` and check
     the step's compiled ``agent`` / ``config`` against the expectation.
  4. With ``--run``: publish the cases that compile, run each one, and report
     whether the connector step finished. A step parked ``awaiting`` forever is
     the hub resume race (see the FortiSOC agent notes), not a config bug.

Live-verified on a FortiSOC tenant with two agents, a shared config name, and
two configs on one agent (all cases pass).

Usage:
    python agent_config_matrix.py --instance <name>                  # compile-only
    python agent_config_matrix.py --instance <name> --run            # + publish/run
    python agent_config_matrix.py --instance <name> --connector activedirectory \\
        --operation global_search \\
        --params '{"search_object": "User", "search_attr_name": "SamAccount Name",
                   "search_attr_value": "jdoe"}' --run

Requires the playbook compiler: ``pip install "pyfsr[playbooks]"``.
Credentials come from a pyfsr instance registry entry (``--instance``).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pyfsr.authoring import warm_catalog
from pyfsr.instances import InstanceRegistry

COLLECTION = "00 - pyfsr - Agent Config Matrix"


@dataclass
class Case:
    name: str
    on_remote: str | None  # agent NAME as YAML would carry it (None = omitted)
    config: str | None  # config NAME (None = omitted)
    expect_agent: str | None = None  # wire agentId (None = runs on the hub)
    expect_config: str | None = None  # wire config UUID
    expect_error: str | None = None  # substring of the expected compile error


def discover(client, connector: str) -> tuple[dict[str, str], dict[str, list[dict]]]:
    """(agentId -> agent name, location -> [{name, uuid, default}]); location '' = hub."""
    names = {
        a["agentId"]: a["name"]
        for a in client.get("/api/3/agents", params={"$limit": 500}).get("hydra:member", [])
        if a.get("agentId")
    }
    where: dict[str, list[dict]] = {}
    for d in client.connectors.configurations_by_location(connector):
        loc = d["agent"] if d["agent"] in names else ""  # the hub's own agentId counts as hub
        where.setdefault(loc, []).append({"name": d["name"], "uuid": d["config_id"], "default": d["default"]})
    return names, where


def derive_cases(names: dict[str, str], where: dict[str, list[dict]]) -> list[Case]:
    cases: list[Case] = []
    label = lambda loc: names.get(loc, loc) if loc else "hub"  # noqa: E731
    agents = [loc for loc in where if loc]
    for loc, cfgs in where.items():
        for c in cfgs:
            if loc:  # explicit agent + its own config
                cases.append(Case(f"{label(loc)} + {c['name']}", names.get(loc, loc), c["name"], loc, c["uuid"]))
            for other in agents:  # explicit agent + a config it does not hold
                if other != loc and c["name"] not in {x["name"] for x in where[other]}:
                    cases.append(
                        Case(
                            f"{label(other)} + {c['name']} (lives on {label(loc)})",
                            names.get(other, other),
                            c["name"],
                            expect_error="does not exist on",
                        )
                    )
        if loc:  # explicit agent, no config -> its default
            dflt = next((c for c in cfgs if c["default"]), cfgs[0])
            cases.append(Case(f"{label(loc)} + default", names.get(loc, loc), None, loc, dflt["uuid"]))
    by_name: dict[str, list[str]] = {}
    for loc, cfgs in where.items():
        for c in cfgs:
            by_name.setdefault(c["name"], []).append(loc)
    for cname, locs in by_name.items():  # config name alone
        remote = [loc for loc in locs if loc]
        if "" in locs:
            hub = next(c for c in where[""] if c["name"] == cname)
            cases.append(Case(f"{cname} alone (hub wins)", None, cname, None, hub["uuid"]))
        elif len(remote) == 1:
            c = next(c for c in where[remote[0]] if c["name"] == cname)
            cases.append(Case(f"{cname} alone (only on {label(remote[0])})", None, cname, remote[0], c["uuid"]))
        else:
            cases.append(Case(f"{cname} alone (on {len(remote)} agents)", None, cname, expect_error="several agents"))
    return cases


def step_yaml(case: Case, connector: str, operation: str, params: dict) -> str:
    lines = [
        f"- name: Matrix {case.name}",
        "  steps:",
        "  - type: start",
        "    name: Start",
        "    next: probe",
        "  - type: connector",
        "    name: Probe",
        f"    connector: {connector}",
        f"    operation: {operation}",
    ]
    if case.on_remote:
        lines.append(f"    on_remote: {json.dumps(case.on_remote)}")
    if case.config:
        lines.append(f"    config: {json.dumps(case.config)}")
    lines += [
        f"    params: {json.dumps(params)}",
        "    next: resumed",
        "  - type: set_variable",
        "    name: Resumed",
        "    vars: {resumed: 'yes'}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--instance", required=True, help="pyfsr instance registry name")
    p.add_argument("--connector", default="activedirectory")
    p.add_argument("--operation", default="global_search")
    p.add_argument(
        "--params",
        default=json.dumps(
            {"search_object": "User", "search_attr_name": "SamAccount Name", "search_attr_value": "jdoe"}
        ),
    )
    p.add_argument("--run", action="store_true", help="publish the compiling cases and run each")
    p.add_argument("--timeout", type=float, default=150)
    args = p.parse_args()
    client = InstanceRegistry.load().client(args.instance)
    params = json.loads(args.params)

    names, where = discover(client, args.connector)
    if not where:
        print(f"no {args.connector} configurations on this box")
        return 1
    for loc, cfgs in where.items():
        print(f"{names.get(loc, loc) if loc else 'hub':24} {[c['name'] for c in cfgs]}")
    cases = derive_cases(names, where)
    # Warm ONCE, then compile every case against that catalog. compile_yaml's
    # default re-warms from the box on every call (teams, picklists, tags, the
    # per-connector fan-out, agents) -- nine compiles would mean nine warms.
    catalog = Path(tempfile.gettempdir()) / "pyfsr_agent_matrix.db"
    warm_catalog(client, catalog)
    head = f"collection: {json.dumps(COLLECTION)}\nvisible: true\nplaybooks:\n"

    failed, runnable = 0, []
    for case in cases:
        res = client.workflow_collections.compile_yaml(
            head + step_yaml(case, args.connector, args.operation, params), db_path=catalog
        )
        errs = [getattr(e, "message", str(e)) for e in (res.errors or [])]
        if case.expect_error:
            ok = (not res.ok) and any(case.expect_error in m for m in errs)
            detail = errs[:1]
        else:
            wf = res.fsr_json["data"][0]["workflows"][0] if res.ok else {"steps": []}
            a = next((s["arguments"] for s in wf["steps"] if s["name"] == "Probe"), {})
            ok = res.ok and a.get("agent") == case.expect_agent and a.get("config") == case.expect_config
            detail = errs[:1] if not res.ok else f"agent={a.get('agent')} config={a.get('config')}"
            if ok:
                runnable.append(case)
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  compile  {case.name}  {detail}")

    if args.run and runnable:
        res = client.workflow_collections.compile_yaml(
            head + "".join(step_yaml(c, args.connector, args.operation, params) for c in runnable), db_path=catalog
        )
        client.workflow_collections.deploy(res.fsr_json, overwrite_changed=True)
        for case in runnable:
            try:
                run = client.playbooks.run_and_wait(f"Matrix {case.name}", timeout=args.timeout)
                st = {s.name: s.status for s in run.steps}
                ok = run.status == "finished" and st.get("Resumed") == "finished"
                print(f"{'PASS' if ok else 'FAIL'}  run      {case.name}  {run.status} Probe={st.get('Probe')}")
            except TimeoutError as exc:
                ok = False
                print(f"FAIL  run      {case.name}  {exc}")
            failed += not ok
    print(f"\n{len(cases)} case(s), {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
