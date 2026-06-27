"""Structural playbook queries — how far you can interrogate playbook *shape*.

FortiSOAR exposes three query tiers; this example tours all three and shows
exactly which questions each can (and cannot) answer.

  1. **Server filter API** (``playbooks.find`` / ``records.query``) — single and
     cross-relationship AND, OR trees, ``$search``. Cheap, but ``steps.arguments``
     is one JSON column matched by substring: it cannot require two facets on the
     *same* step, count steps, or join parent↔child.

  2. **Server aggregate API** (``records.aggregate``) — ``groupby`` + ``count`` /
     ``sum`` / ``avg`` pushed to the database. Great for "playbooks by trigger
     type". But there is **no HAVING**, and grouping the steps module by its
     parent workflow is rejected server-side — so per-playbook step counts can't
     be done here.

  3. **Client-side structural matcher** (``playbooks.match`` /
     ``playbooks.match_across`` + the :mod:`pyfsr.playbook_match` predicates) —
     parses each definition's steps and evaluates composable predicates. This is
     the only tier that does same-step precision, quantities, boolean mixes, and
     parent/child joins.

Run:
    python examples/playbook_structural_queries.py          # offline explainer
    FSR_BASE_URL=https://box:13006 FSR_USERNAME=csadmin FSR_PASSWORD=... \\
        python examples/playbook_structural_queries.py      # + live tour
"""

from __future__ import annotations

import os

from pyfsr import FortiSOAR
from pyfsr.playbook_match import all_of, count, has, parse_playbook, step, trigger


def offline_tour() -> None:
    """Build the predicates and explain what each tier can express. No creds."""
    print("=" * 70)
    print("TIER 3 — structural predicates (pyfsr.playbook_match)")
    print("=" * 70)

    # Same-step precision: fortigate AND block_ip on ONE step (find() would also
    # match a playbook with fortigate in step 1 and block_ip in step 5).
    same_step = has(step(connector="fortigate", operation="block_ip"))
    print("\n[same-step]  has(step(connector='fortigate', operation='block_ip'))")

    # Quantities: exactly 2 set-variable steps AND exactly 1 code-snippet.
    quantity = all_of(
        count(step(step_type="set_variable"), n=2),
        count(step(step_type="code_snippet"), n=1),
    )
    print("[quantity]   2x set_variable AND 1x code_snippet")

    # Boolean mix: a manual playbook that blocks an IP on the same fortigate step.
    combined = all_of(trigger("manual"), same_step)
    print("[combined]   manual trigger AND same-step fortigate+block_ip")

    # Prove the predicates on a synthetic definition (the wire shape find() returns).
    demo = {
        "name": "Demo",
        "uuid": "demo-1",
        "steps": [
            {"name": "Start", "stepType": {"name": "cybersponse.action"}, "arguments": {}},
            {"name": "Set A", "stepType": {"name": "SetVariable"}, "arguments": {}},
            {"name": "Set B", "stepType": {"name": "SetVariable"}, "arguments": {}},
            {"name": "Code", "stepType": {"name": "CodeSnippet"}, "arguments": {}},
            {
                "name": "Block",
                "stepType": {"name": "Connectors"},
                "arguments": {"connector": "fortigate-firewall", "operation": "block_ip"},
            },
        ],
    }
    pb = parse_playbook(demo)
    print(f"\n  parsed trigger={pb.trigger_type!r}, {len(pb.steps)} steps")
    print(f"  same_step -> {same_step(pb)}")
    print(f"  quantity  -> {quantity(pb)}")
    print(f"  combined  -> {combined(pb)}")


def live_tour() -> None:
    """Run all three tiers against a real box (needs FSR_BASE_URL/USERNAME/PASSWORD)."""
    base = os.environ.get("FSR_BASE_URL")
    user = os.environ.get("FSR_USERNAME")
    pw = os.environ.get("FSR_PASSWORD")
    if not (base and user and pw):
        print("\n(skipping live tour — set FSR_BASE_URL/FSR_USERNAME/FSR_PASSWORD)")
        return

    verify = os.environ.get("FSR_VERIFY_SSL", "false").lower() in ("1", "true", "yes")
    c = FortiSOAR(base, username=user, password=pw, verify_ssl=verify)
    pb = c.playbooks
    print("\n" + "=" * 70)
    print(f"LIVE — {c.base_url} (version {c.version()})")
    print("=" * 70)

    # TIER 1: server filter — action used + cross-relationship AND.
    print("\n[tier 1] manual trigger AND a block_ip op (server filter):")
    for p in pb.find(trigger_type="manual", uses_operation="block_ip", limit=2000)[:5]:
        print("   -", p.get("name"))

    # TIER 2: server aggregate — playbooks by trigger type.
    print("\n[tier 2] playbooks grouped by trigger type (server aggregate):")
    for row in c.records("workflows").aggregate(group_by="triggerStep.stepType.name", count=True):
        print(f"   {row.get('name'):32} {row.get('total')}")

    # TIER 3a: same-step precision (fortigate AND block_ip on one step).
    same_step = has(step(connector="fortigate", operation="block_ip"))
    hits = pb.match(same_step, prefilter={"uses_connector": "fortigate"})
    print(f"\n[tier 3] same-step fortigate+block_ip: {len(hits)}")
    for p in hits[:5]:
        print("   -", p.get("name"))

    # TIER 3b: quantity — >=2 set-variable AND >=1 code-snippet.
    quantity = all_of(count(step(step_type="set_variable"), min=2), count(step(step_type="code_snippet"), min=1))
    qhits = pb.match(quantity)
    print(f"\n[tier 3] >=2 set_variable AND >=1 code_snippet: {len(qhits)}")
    for p in qhits[:5]:
        parsed = parse_playbook(p)
        sv = sum(1 for s in parsed.steps if s.step_type_raw == "SetVariable")
        cs = sum(1 for s in parsed.steps if s.step_type_raw == "CodeSnippet")
        print(f"   - {p.get('name')}  (set_var={sv}, code_snippet={cs})")

    # TIER 3c: parent/child join — manual parent whose referenced child blocks an IP.
    cross = pb.match_across(trigger("manual"), has(step(operation="block_ip")))
    print(f"\n[tier 3] manual parents whose referenced child does block_ip: {len(cross)}")
    for p in cross[:5]:
        print("   -", p.get("name"))


if __name__ == "__main__":
    offline_tour()
    live_tour()
