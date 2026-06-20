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

  * ``live_tour()`` actually executes a variety of queries against a real box,
    exercising filter(), first(), count(), exists(), iterate(), typed models,
    raw=True, and nested OR groups. It only runs when FSR_BASE_URL /
    FSR_USERNAME / FSR_PASSWORD are set in the env.

Run:
    python examples/queries.py            # offline tour (no creds needed)
    FSR_BASE_URL=https://10.0.0.5 FSR_USERNAME=admin FSR_PASSWORD=... \\
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


def hr(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(title)
    print("=" * 72)


# ===========================================================================
# OFFLINE TOUR — no appliance required
# ===========================================================================


def offline_tour() -> None:
    hr("OFFLINE TOUR — building queries and inspecting the knowledge bases")

    # -- 1. Single equality ---------------------------------------------------
    # severity.itemValue dot-walks the picklist relationship to its display name.
    show(
        "1. Critical alerts (single equality + limit)",
        Query().eq("severity.itemValue", "Critical").limit(25),
    )

    # -- 2. Comparison operators + sorting ------------------------------------
    # createDate is an epoch float (seconds). gt/gte/lt/lte work on numbers and dates.
    last_24h = time.time() - 86_400
    show(
        "2. Alerts created in the last 24h, newest first",
        Query().gt("createDate", last_24h).sort("createDate", "DESC").limit(50),
    )

    # -- 3. Chained AND conditions --------------------------------------------
    show(
        "3. Open, high-severity alerts (implicit AND)",
        Query().eq("status.itemValue", "Open").eq("severity.itemValue", "High").limit(50),
    )

    # -- 4. Set membership: in_ / nin -----------------------------------------
    show(
        "4. Critical or High severity, excluding Test/Benign types",
        Query().in_("severity.itemValue", ["High", "Critical"]).nin("type.itemValue", ["Test", "Benign"]),
    )

    # -- 5. Text matching: like / notlike ------------------------------------
    show(
        "5. Name contains 'phish' (case-insensitive substring)",
        Query().like("name", "phish").limit(20),
    )

    # -- 6. Presence checks: exists / isnull ---------------------------------
    # isnull(False) = "is NOT null" — there is no isnotnull operator (400s on appliance).
    show(
        "6. Assigned and severity present",
        Query().isnull("assignedTo", False).exists("severity", True),
    )

    # -- 7. OR groups via group() --------------------------------------------
    # status Open AND (severity High OR Critical)
    high_or_crit = Query("OR").eq("severity.itemValue", "High").eq("severity.itemValue", "Critical")
    show(
        "7. Open AND (High OR Critical) — nested OR group",
        Query().eq("status.itemValue", "Open").group(high_or_crit),
    )

    # -- 8. Deeper nesting + comparisons inside a group ----------------------
    recent_unassigned = Query("AND").gt("createDate", last_24h).isnull("assignedTo", True)
    show(
        "8. Critical OR (created last 24h AND unassigned)",
        Query("OR").eq("severity.itemValue", "Critical").group(recent_unassigned),
    )

    # -- 9. Field-path validation (pass module=) ------------------------------
    print("\n# 9. Local field-path validation with Query(module='alerts')")
    ok = Query(module="alerts").eq("severity.itemValue", "Critical")
    print("  valid  :", ok.to_body()["filters"][0]["field"])
    for bad in ("nonexistent_field", "name.itemValue"):  # typo / dot-walk into scalar
        try:
            Query(module="alerts").eq(bad, "x")
        except ValueError as exc:
            print(f"  rejected {bad!r}: {exc}")

    # -- 10. Field projection: select / ignore --------------------------------
    show(
        "10. Return only a few fields per record (__selectFields)",
        Query().eq("status.itemValue", "Open").select("uuid", "name", "severity"),
    )
    show(
        "10b. Strip large text fields (__ignoreFields)",
        Query().eq("status.itemValue", "Open").ignore("description", "sourcedata"),
    )

    # -- 11. Sort, search, multi-field sort -----------------------------------
    show(
        "11. Full-text search + structured filter + multi-field sort",
        Query()
        .eq("status.itemValue", "Open")
        .search("ransomware")
        .sort("severity.orderIndex", "ASC")
        .sort("createDate", "DESC")
        .limit(20),
    )

    # -- 12. Trigger-condition operators (playbook filters) ------------------
    show(
        "12. Trigger filter: status changed AND tags contain all of {a,b}",
        Query().changed("status").in_all("tags", ["a", "b"]),
    )

    # -- 13. The typed body object (Query.model()) ---------------------------
    q = Query().eq("name", "x").limit(5)
    body = q.model()
    print("\n# 13. Query.model() -> typed QueryBody")
    print(f"  type={type(body).__name__} logic={body.logic} limit={body.limit} filters={len(body.filters)}")

    # -- 14. Operator knowledge base ------------------------------------------
    print("\n# 14. Operator knowledge base (OPERATOR_SPECS)")
    for name in ("eq", "in", "exists", "changed", "in_all"):
        spec = OPERATOR_SPECS[name]
        print(f"  {name:8s} arity={spec.arity.value:7s} category={spec.category:7s} — {spec.summary}")
    # Arity is enforced: wrong shapes are rejected before hitting the wire.
    for op, val in (("in", "not-a-list"), ("eq", None), ("isnotnull", True)):
        try:
            Query().where("f", op, val)
        except ValueError as exc:
            print(f"  where('f', {op!r}, {val!r}) -> {exc}")

    # -- 15. Field / relationship knowledge base ------------------------------
    print("\n# 15. Field KB (pyfsr.fields)")
    print(f"  alerts has {len(field_kb.module_fields('alerts'))} known fields")
    rels = field_kb.module_relationships("alerts")
    sample = list(rels.items())[:4]
    print(f"  some alerts relationships -> {sample}")
    norm = field_kb.normalize_field_path("severity__itemValue")
    print(f"  normalize 'severity__itemValue' -> {norm!r}")

    # -- 16. Raw dict bodies still work ---------------------------------------
    print("\n# 16. A hand-written body dict is accepted by RecordSet.query() too")
    raw = {
        "logic": "AND",
        "filters": [{"field": "name", "operator": "like", "value": "vpn"}],
    }
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

    hr(f"LIVE TOUR — querying {base}")

    # A) filter() — the idiomatic entry point for structured queries ----------
    # Returns HydraPage[Alert]; iteration yields typed Alert models.
    page = alerts.filter(Query().sort("createDate", "DESC").limit(5))
    print(f"\nA) filter() — {page.total} alerts total, {len(page)} on this page")
    for a in page:
        owner = a.create_user.name if a.create_user else "—"
        # a.severity is PicklistIRI | None — a typed string, not generic Any
        sev_iri = a.severity or "(no severity)"
        print(f"   {a.name!r:40.40}  severity_iri={sev_iri[-36:]}  created_by={owner}")

    # B) first() — returns first match or None, no indexing needed -----------
    latest = alerts.first(Query().sort("createDate", "DESC"))
    print(f"\nB) first() — most recent alert: {latest.name if latest else 'none'!r}")

    unassigned = alerts.first(Query().isnull("assignedTo", True).sort("createDate", "DESC"))
    print(f"   first unassigned: {unassigned.name if unassigned else 'none'!r}")

    # C) count() — total items, limit=1 fetch (cheap) ------------------------
    total = alerts.count()
    open_count = alerts.count(Query().eq("status.itemValue", "Open"))
    crit_count = alerts.count(Query().in_("severity.itemValue", ["Critical", "High"]))
    print(f"\nC) count() — total={total}  open={open_count}  crit/high={crit_count}")

    # D) exists() — boolean check, no count parsing needed -------------------
    has_open = alerts.exists(Query().eq("status.itemValue", "Open"))
    has_critical = alerts.exists(Query().eq("severity.itemValue", "Critical"))
    print(f"\nD) exists() — any open: {has_open}  any critical: {has_critical}")

    # E) Nested OR group — open AND (Critical OR High) -----------------------
    sev_filter = Query("OR").eq("severity.itemValue", "Critical").eq("severity.itemValue", "High")
    q = Query().eq("status.itemValue", "Open").group(sev_filter).sort("createDate", "DESC")
    urgent = alerts.filter(q.limit(5))
    print(f"\nE) Nested OR group — open Critical/High: {urgent.total} total")
    for a in urgent:
        print(f"   {a.name!r:40.40}")

    # F) iterate() — lazy streaming across pages, capped --------------------
    print("\nF) iterate() — streaming up to 8 alerts:")
    for i, a in enumerate(alerts.iterate(Query().sort("createDate", "DESC"), max_records=8)):
        print(f"   {i:2d}. {a.name!r:50.50}")

    # G) raw=True — get plain dicts instead of typed models ------------------
    raw_page = alerts.filter(Query().limit(3), raw=True)
    print(f"\nG) raw=True — members are plain dicts: {type(raw_page.members[0]).__name__}")
    print(f"   keys: {sorted(raw_page.members[0].keys())[:6]} ...")

    # H) Projection: fields= trims the HydraPage members to a plain dict -----
    slim = alerts.filter(
        Query().sort("createDate", "DESC").limit(5),
        fields=["uuid", "name"],
    )
    print(f"\nH) fields= projection — type={type(slim).__name__}  members={len(slim['members'])}")
    print(f"   first keys: {sorted(slim['members'][0].keys())}")

    # I) Typed model attribute access -----------------------------------------
    # Get one alert with relationships expanded so assignedTo is a full User.
    sample = alerts.first(Query().isnull("assignedTo", False))
    if sample:
        assigned = sample.assigned_to
        print("\nI) Typed model — assignedTo expanded:")
        print(f"   alert.name = {sample.name!r}")
        print(f"   alert.severity (PicklistIRI) = {sample.severity!r}")
        if assigned:
            print(f"   alert.assigned_to.name = {assigned.name!r}")
    else:
        print("\nI) No assigned alerts found for typed model demo")

    # J) select() — slim the payload to only needed fields --------------------
    slim_q = (
        Query()
        .eq("status.itemValue", "Open")
        .select("uuid", "name", "severity", "createDate")
        .sort("createDate", "DESC")
        .limit(5)
    )
    slim_page = alerts.filter(slim_q)
    print(f"\nJ) select() — {len(slim_page)} records, fields trimmed by server")
    if slim_page.members:
        a = slim_page.members[0]
        print(f"   uuid={a.uuid}  name={a.name!r}  severity={a.severity!r}")

    # K) isnull edge cases ----------------------------------------------------
    # isnull(False) = "field is NOT null"  — use this instead of isnotnull (400s)
    has_assigned = alerts.exists(Query().isnull("assignedTo", False))  # assignedTo not null
    has_unassigned = alerts.exists(Query().isnull("assignedTo", True))  # assignedTo is null
    n_assigned = alerts.count(Query().isnull("assignedTo", False))
    print(f"\nK) isnull — any assigned: {has_assigned}  any unassigned: {has_unassigned}  assigned count: {n_assigned}")

    # L) HydraPage properties -------------------------------------------------
    last_page = alerts.filter(Query().sort("createDate", "DESC").limit(10))
    print(
        f"\nL) HydraPage — total={last_page.total}  count={last_page.count}  "
        f"has_next={last_page.has_next}  len()={len(last_page)}"
    )

    print("\n✓ Live tour complete")


def main() -> None:
    offline_tour()
    live_tour()


if __name__ == "__main__":
    main()
