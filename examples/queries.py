"""A guided tour of the pyfsr Query DSL — from one-liners to relationship-aware,
paginated, projected queries.

The Query builder assembles the body for ``POST /api/query/{module}`` (the same
endpoint the FortiSOAR "Advanced Search" uses). Every mutator returns ``self``
so calls chain, and the whole thing validates *before* it ever hits the wire:
unknown operators, wrong value shapes (a list where a scalar belongs), and —
when you pass ``module=`` — bogus field paths are all rejected locally.

This file is split in two:

  * ``offline_tour()`` builds queries and prints the exact JSON body they render
    to, plus introspects the operator and field knowledge bases. It needs NO
    appliance and always runs — read it top to bottom to learn the DSL.

  * ``live_tour()`` actually executes a few of them against a real box. It only
    runs when FSR_BASE_URL / FSR_USERNAME / FSR_PASSWORD are set in the env.

Run:
    python examples/queries.py            # offline tour (no creds needed)
    FSR_BASE_URL=https://10.0.0.5 FSR_USERNAME=admin FSR_PASSWORD=... \
        python examples/queries.py        # offline + live tour
"""

from __future__ import annotations

import json
import os
import time

from pyfsr import OPERATOR_SPECS, Query
from pyfsr import fields as field_kb


def show(title: str, query: Query) -> None:
    """Print a labelled query and the JSON body it POSTs."""
    print(f"\n# {title}")
    print(json.dumps(query.to_body(), indent=2, default=str))


# ===========================================================================
# OFFLINE TOUR — no appliance required
# ===========================================================================


def offline_tour() -> None:
    print("=" * 72)
    print("OFFLINE TOUR — building queries and inspecting the knowledge bases")
    print("=" * 72)

    # -- 1. The simplest thing: one equality filter --------------------------
    # `severity.itemValue` dot-walks the severity *picklist* relationship to its
    # display value. `eq` is the default workhorse operator.
    show(
        "1. Critical alerts (single equality + limit)",
        Query().eq("severity.itemValue", "Critical").limit(25),
    )

    # -- 2. Comparison operators + sorting -----------------------------------
    # Numeric/date comparisons: gt/gte/lt/lte. createDate is epoch milliseconds.
    last_24h = int((time.time() - 86_400) * 1000)
    show(
        "2. Alerts created in the last 24h, newest first",
        Query().gt("createDate", last_24h).sort("createDate", "DESC").limit(50),
    )

    # -- 3. Several filters AND-ed together ----------------------------------
    # Chained leaves join with the top-level logic (AND by default).
    show(
        "3. Open, high-severity alerts (implicit AND)",
        Query().eq("status.itemValue", "Open").eq("severity.itemValue", "High").limit(50),
    )

    # -- 4. Set membership: in / nin -----------------------------------------
    # `in` = any-of; `nin` = none-of. Both take a list (a scalar is rejected).
    show(
        "4. Alerts in a set of severities, excluding some types",
        Query()
        .in_("severity.itemValue", ["High", "Critical"])
        .nin("type.itemValue", ["Test", "Benign"]),
    )

    # -- 5. Text matching: like / contains -----------------------------------
    # `like` is a case-insensitive substring match on a scalar string field.
    show(
        "5. Name contains 'phish' (case-insensitive)",
        Query().like("name", "phish").limit(20),
    )

    # -- 6. Presence checks: exists / isnull ---------------------------------
    # These take a bool. For "is NOT null", use isnull with value=False — there
    # is no `isnotnull` operator (it 400s on the appliance).
    show(
        "6. Alerts that are assigned (assignedTo is not null)",
        Query().isnull("assignedTo", False).exists("severity", True),
    )

    # -- 7. OR groups via nested queries -------------------------------------
    # group() nests another Query as a sub-filter with its own logic. Here:
    # status Open AND (severity High OR Critical).
    high_or_crit = Query("OR").eq("severity.itemValue", "High").eq("severity.itemValue", "Critical")
    show(
        "7. Open AND (High OR Critical) — nested OR group",
        Query().eq("status.itemValue", "Open").group(high_or_crit),
    )

    # -- 8. Deeper nesting + comparison inside a group -----------------------
    recent_unassigned = Query("AND").gt("createDate", last_24h).isnull("assignedTo", True)
    show(
        "8. Critical OR (recent AND unassigned)",
        Query("OR").eq("severity.itemValue", "Critical").group(recent_unassigned),
    )

    # -- 9. Field-path validation (pass module=) -----------------------------
    # With module="alerts", field paths are checked against the shipped schema
    # KB. Relationship dot-walks are allowed; typos and walking into a scalar
    # are caught locally — before any network call.
    print("\n# 9. Local field-path validation with Query(module='alerts')")
    ok = Query(module="alerts").eq("severity.itemValue", "Critical")
    print("  valid  :", ok.to_body()["filters"][0]["field"])
    for bad in ("nonexistent_field", "name.itemValue"):  # typo / dot-walk into scalar
        try:
            Query(module="alerts").eq(bad, "x")
        except ValueError as exc:
            print(f"  rejected {bad!r}: {exc}")

    # -- 10. Projection: select / ignore -------------------------------------
    # Trim the payload to just the fields you need (token- and bandwidth-cheap).
    # select() and ignore() are mutually exclusive.
    show(
        "10. Return only a few fields per record (__selectFields)",
        Query().eq("status.itemValue", "Open").select("uuid", "name", "severity"),
    )

    # -- 11. Trigger-condition operators (playbook start filters) ------------
    # `changed` (value-less) and `in_all` (contains ALL) are meant for playbook
    # start/update trigger filters rather than ad-hoc search, but the builder
    # produces their wire shape the same way.
    show(
        "11. Trigger filter: status changed AND tags contain all of {a,b}",
        Query().changed("status").in_all("tags", ["a", "b"]),
    )

    # -- 12. The typed body object (Query.model()) ---------------------------
    # to_body() is the dict; model() is the validated pydantic QueryBody behind
    # it — handy for programmatic inspection or passing around typed.
    q = Query().eq("name", "x").limit(5)
    body = q.model()
    print("\n# 12. Query.model() -> typed QueryBody")
    print(
        f"  type={type(body).__name__} logic={body.logic} limit={body.limit} "
        f"filters={len(body.filters)}"
    )

    # -- 13. Operator knowledge base -----------------------------------------
    print("\n# 13. Operator knowledge base (OPERATOR_SPECS)")
    for name in ("eq", "in", "exists", "changed", "in_all"):
        spec = OPERATOR_SPECS[name]
        print(
            f"  {name:8s} arity={spec.arity.value:7s} category={spec.category:7s} — {spec.summary}"
        )
    # Arity is enforced: a list operator rejects a scalar, etc.
    for op, val in (("in", "not-a-list"), ("eq", None), ("isnotnull", True)):
        try:
            Query().where("f", op, val)
        except ValueError as exc:
            print(f"  where('f', {op!r}, {val!r}) -> {exc}")

    # -- 14. Field / relationship knowledge base -----------------------------
    print("\n# 14. Field KB (pyfsr.fields)")
    print(f"  alerts has {len(field_kb.module_fields('alerts'))} known fields")
    rels = field_kb.module_relationships("alerts")
    sample = list(rels.items())[:4]
    print(f"  some alerts relationships -> {sample}")
    norm = field_kb.normalize_field_path("severity__itemValue")
    print(f"  normalize 'severity__itemValue' -> {norm}")

    # -- 15. Raw dict bodies still work --------------------------------------
    # Power users can hand a raw body dict straight to RecordSet.query(); the
    # builder is a convenience, not a requirement.
    print("\n# 15. A hand-written body dict is accepted by RecordSet.query() too")
    raw = {"logic": "AND", "filters": [{"field": "name", "operator": "like", "value": "vpn"}]}
    print(json.dumps(raw, indent=2))


# ===========================================================================
# LIVE TOUR — needs a real appliance (env-gated)
# ===========================================================================


def live_tour() -> None:
    base = os.environ.get("FSR_BASE_URL")
    user = os.environ.get("FSR_USERNAME")
    pwd = os.environ.get("FSR_PASSWORD")
    if not (base and user and pwd):
        print("\n(skipping live tour — set FSR_BASE_URL / FSR_USERNAME / FSR_PASSWORD)")
        return

    import urllib3

    from pyfsr import FortiSOAR

    urllib3.disable_warnings()
    client = FortiSOAR(base, username=user, password=pwd, verify_ssl=False)
    alerts = client.records("alerts")

    print("\n" + "=" * 72)
    print(f"LIVE TOUR — querying {base}")
    print("=" * 72)

    # A) One page of recent alerts, typed models back.
    page = alerts.query(Query().sort("createDate", "DESC").limit(5))
    print(f"\nA) {page.total} alerts total; showing {page.count} newest:")
    for a in page.members:
        # typed model: attribute access + dict-style + typed relationship accessor
        owner = a.create_user.name if a.create_user else "—"
        print(f"   {a['name']!r:40.40}  created_by={owner}")

    # B) Projection: only pull the fields we print (cheaper payload).
    slim = alerts.query(
        Query().sort("createDate", "DESC").limit(5),
        fields=["uuid", "name"],
    )
    print(f"\nB) Same page, projected to uuid+name: {len(slim['members'])} rows")

    # C) Module-validated query: a typo here would have raised locally.
    q = Query(module="alerts").isnull("assignedTo", True).sort("createDate", "DESC")
    unassigned = alerts.query(q.limit(5))
    print(f"\nC) Unassigned alerts: {unassigned.total} total")

    # D) iterate(): stream across pages, capped, without manual paging.
    print("\nD) Streaming up to 12 alerts via iterate():")
    for i, a in enumerate(alerts.iterate(Query().sort("createDate", "DESC"), max_records=12)):
        print(f"   {i:2d}. {a['name']!r:50.50}")


def main() -> None:
    offline_tour()
    live_tour()


if __name__ == "__main__":
    main()
