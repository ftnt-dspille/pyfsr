#!/usr/bin/env python
"""Stand up FortiSOAR's TAXII server as a live threat feed a FortiGate can pull.

FortiSOAR ships a **native TAXII 2.1 server** that *serves* outgoing feeds — not
just the connectors that ingest them. Once it's on, a FortiGate can point its
``system external-resource`` straight at a FortiSOAR dataset and pull indicators
on its refresh interval, with **no FortiManager file-writing and no policy push**.
Creating a threat-intel record in FortiSOAR is then the entire "block it" action.

This script performs the whole setup end-to-end and is safe to re-run:

  1. enable the TAXII server                (system_settings)
  2. create an API-key user + binding       (client.api_keys.ensure_usable)
  3. create a "Block List (IP Address)" dataset — a dataset *is* a TAXII
     collection                             (client.system_queries.ensure)
  4. add an IP indicator to the feed module (threat_intel_feeds)
  5. query the dataset back                 (client.taxii.objects)
  6. re-fetch it exactly the way a FortiGate does (HTTP Basic) and print the
     matching ``config system external-resource`` block

Usage:
    python examples/taxii_threat_feed_to_fortigate.py \\
        --host fortisoar.example.com --user csadmin --password changeme

    # add a specific indicator
    python examples/taxii_threat_feed_to_fortigate.py ... --indicator 198.51.100.24

    # tear down what this script created
    python examples/taxii_threat_feed_to_fortigate.py ... --cleanup

Environment variables:
    FSR_BASE_URL, FSR_USERNAME, FSR_PASSWORD

Notes / gotchas this script encodes (all live-verified):

* **Auth from a FortiGate.** A FortiGate cannot send a custom header, so the
  ``X-API-KEY-<name>: <key>`` form is unreachable from it. FortiSOAR supports a
  basic-auth fallback made for exactly this: **username =**
  ``X-API-KEY-<api_key_name>``, **password =** ``<api-key>``. That is what
  step 6 exercises.
* **The plaintext key is returned only at creation time.** Capture it then or
  create a new one.
* **A dataset is a collection.** The collection id *is* the ``system_queries``
  uuid — which is why ``client.system_queries`` and ``client.taxii`` are two
  views of one thing. ``client.taxii`` is read-only (it *serves* the feed);
  ``client.system_queries`` is what defines a collection.
* **Unlisted datasets still serve.** ``/collections`` only lists a subset
  (visibility is per-caller), but ``/collections/<uuid>/objects`` works for any
  dataset uuid — don't panic if a new dataset isn't in the listing.
* **The vendor Block List filter is a rolling 24h window**
  (``createDate > now-24h``), which doubles as indicator expiry: an indicator
  ages out of the feed on its own. Pass ``--no-expiry`` to drop that filter.
* **Objects carry a clean scalar ``value``** (``pattern`` is null), so a
  FortiGate maps them with ``object-array-path $.objects`` +
  ``address-data-field $.value`` — no STIX pattern parsing required.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.request

from pyfsr import FortiSOAR

# The TAXII server on/off switch is a fixed, shipped system_settings record.
TAXII_SETTINGS_UUID = "18f3043e-c1e4-4041-bba3-d481ea86d1b5"
DATASET_NAME = "Block List (IP Address)"
API_KEY_NAME = "fortigate-threat-feed-key"
FEED_MODULE = "threat_intel_feeds"


# --------------------------------------------------------------------------- #
# 1. TAXII server
# --------------------------------------------------------------------------- #
def enable_taxii(client: FortiSOAR) -> bool:
    """Turn the TAXII server on. Returns True if it was already enabled."""
    s = client.get(f"/api/3/system_settings/{TAXII_SETTINGS_UUID}/", params={"$relationships": "true"})
    already = bool((s.get("publicValues") or {}).get("status", {}).get("enabled"))
    if already:
        print("  TAXII server: already enabled")
        return True
    s.setdefault("publicValues", {}).setdefault("status", {})["enabled"] = True
    client.put(f"/api/3/system_settings/{TAXII_SETTINGS_UUID}", data=s)
    print("  TAXII server: enabled")
    return False


# --------------------------------------------------------------------------- #
# 2. API key
# --------------------------------------------------------------------------- #
def ensure_api_key(client: FortiSOAR, *, roles: list[str], teams: list[str]) -> tuple[dict, str | None]:
    """Create the API-key user + binding, returning (binding, plaintext_key).

    ``plaintext`` is only ever returned at creation time; on a re-run of an
    existing key it comes back None and you must mint a new one to get material.
    """
    binding, plaintext = client.api_keys.ensure_usable(
        name=API_KEY_NAME,
        roles=roles,
        teams=teams,
        api_key_validity=365,
    )
    print(f"  api key: {binding.get('name')} (binding {binding.get('uuid')})")
    if plaintext:
        print(f"  plaintext key: {plaintext}   <-- capture now, shown only at creation")
    else:
        print("  plaintext key: <not returned — key already existed; re-create to get material>")
    return binding, plaintext


# --------------------------------------------------------------------------- #
# 3. Dataset == TAXII collection
# --------------------------------------------------------------------------- #
def _picklist(client: FortiSOAR, field: str, value: str) -> str:
    """Friendly picklist value -> IRI, via pyfsr's live resolver."""
    return client.picklists.resolve_record_fields(FEED_MODULE, {field: value})[field]


def create_dataset(client: FortiSOAR, *, expiry: bool = True):
    """Create the vendor-shaped 'Block List (IP Address)' dataset (idempotent).

    A dataset **is** the TAXII collection: the id in
    ``/api/taxii/1/collections/<id>/objects`` is this record's uuid.

    ``client.system_queries.filter()`` sets each filter's ``type`` and
    ``.ensure()`` builds the body with ``logic`` — both mandatory, since the
    appliance silently ignores filters that omit them.
    """
    sq = client.system_queries
    filters = [
        sq.filter("typeOfFeed", "eq", _picklist(client, "typeOfFeed", "IP Address")),
        sq.filter("confidence", "gte", 70),
        sq.filter("reputation", "eq", _picklist(client, "reputation", "Malicious")),
    ]
    if expiry:
        # Rolling 24h window — the vendor default. Doubles as indicator expiry:
        # an indicator drops out of the feed on its own after a day.
        filters.append(
            sq.filter(
                "createDate",
                "gt",
                "{{getRelativeDate(0,0,-24,'end','end','end')}}",
                type="datetime",
            )
        )

    before = sq.find_by_name(DATASET_NAME, module=FEED_MODULE)
    ds = sq.ensure(name=DATASET_NAME, module=FEED_MODULE, filters=filters)
    verb = "reusing" if before else "created"
    print(f"  dataset: {verb} {ds['name']} ({ds['uuid']})  module={ds.module}")
    return ds


# --------------------------------------------------------------------------- #
# 4. Add an indicator to the feed
# --------------------------------------------------------------------------- #
def add_indicator(client: FortiSOAR, ip: str, *, source: str) -> dict:
    """Add one malicious IP. It must clear the dataset filter to appear:
    typeOfFeed=IP Address, confidence >= 70, reputation=Malicious."""
    fields = client.picklists.resolve_record_fields(
        FEED_MODULE,
        {
            "value": ip,
            "name": ip,
            "typeOfFeed": "IP Address",
            "reputation": "Malicious",
            "confidence": 90,
            "source": source,
            "description": "Added by the pyfsr TAXII threat-feed example.",
        },
    )
    rec = client.post(f"/api/3/{FEED_MODULE}", data=fields)
    print(f"  indicator: {rec['value']} ({rec['uuid']})")
    return rec


# --------------------------------------------------------------------------- #
# 5/6. Read it back — as pyfsr, then as a FortiGate
# --------------------------------------------------------------------------- #
def query_dataset(client: FortiSOAR, dataset_uuid: str) -> list[dict]:
    """Read the collection with pyfsr's (read-only) TAXII API."""
    env = client.taxii.objects(dataset_uuid)
    objs = env.get("objects") or []
    print(f"  collection {dataset_uuid}: totalItems={env.get('totalItems')}")
    for o in objs:
        print(f"    value={o.get('value')!r:20} type={o.get('type')} pattern={o.get('pattern')!r}")
    return objs


def fetch_as_fortigate(base_url: str, dataset_uuid: str, key_name: str, key: str, *, verify_ssl: bool = True) -> dict:
    """Fetch exactly the way a FortiGate does: HTTP Basic, no custom headers.

    username = ``X-API-KEY-<api_key_name>``, password = ``<api-key>``.
    """
    url = f"{base_url.rstrip('/')}/api/taxii/1/collections/{dataset_uuid}/objects"
    token = base64.b64encode(f"X-API-KEY-{key_name}:{key}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    ctx = ssl.create_default_context()
    if not verify_ssl:
        # Opt-in only (--no-verify-ssl), for lab appliances with self-signed certs.
        # Prefer trusting the appliance CA over disabling verification.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read())


def print_fortigate_config(base_url: str, dataset_uuid: str, key_name: str, key: str) -> None:
    """The matching FortiOS config. `generic-address` (type 7) parses JSON via
    JSONPath — which is why a STIX/JSON feed works at all."""
    url = f"{base_url.rstrip('/')}/api/taxii/1/collections/{dataset_uuid}/objects"
    print(
        f"""
config system external-resource
    edit "FortiSOAR-BlockList-IP"
        set status enable
        set type generic-address
        set resource "{url}"
        set object-array-path "$.objects"
        set address-data-field "$.value"
        set address-name-field "$.name"
        set namespace "fortisoar"
        set username "X-API-KEY-{key_name}"
        set password "{key}"
        set refresh-rate 5
    next
end"""
    )


# --------------------------------------------------------------------------- #
def query_by_source(client: FortiSOAR, source: str, *, limit: int = 100) -> list[dict]:
    """Records whose ``source`` equals `source`, with the filter shape that
    actually filters.

    DANGER — read before editing. ``POST /api/query/<module>`` **silently
    ignores a filter** and returns *every* record unless the body carries
    ``logic`` and each filter carries ``type``. Both of these are real:

        {"filters":[{"field":"source","operator":"eq","value":"nope"}]}
            -> ALL records                      # filter dropped on the floor
        {"logic":"AND","filters":[
            {"field":"source","operator":"eq","value":"nope","type":"primitive"}]}
            -> 0 records                        # correct

    A caller that deletes whatever comes back therefore deletes the whole
    module. Callers must still verify each record client-side (see `cleanup`).
    """
    r = client.post(
        f"/api/query/{FEED_MODULE}",
        data={
            "limit": limit,
            "logic": "AND",
            "filters": [{"field": "source", "operator": "eq", "value": source, "type": "primitive"}],
        },
    )
    return r.get("hydra:member", [])


def cleanup(client: FortiSOAR, *, source: str) -> None:
    """Remove what this example created: indicators, dataset, api key.

    Deliberately paranoid: the server-side filter is re-checked client-side and
    anything that doesn't match `source` exactly is skipped, so a regression in
    the filter (see `query_by_source`) can never turn this into "delete all".
    """
    candidates = query_by_source(client, source)

    # Belt and braces: never delete a record the filter shouldn't have returned.
    mine = [r for r in candidates if r.get("source") == source]
    foreign = len(candidates) - len(mine)
    if foreign:
        print(
            f"  !! filter returned {foreign} record(s) whose source != {source!r} — "
            "server-side filtering is not being applied; skipping those."
        )
    if not mine:
        print(f"  no indicators with source={source!r}")

    for rec in mine:
        client.delete(f"/api/3/{FEED_MODULE}/{rec['uuid']}")
        print(f"  deleted indicator {rec.get('value')}")
    ds = client.system_queries.find_by_name(DATASET_NAME, module=FEED_MODULE)
    if ds:
        client.system_queries.delete(ds["uuid"])
        print(f"  deleted dataset {ds['uuid']}")
    for b in client.get("/api/3/api_keys", params={"$limit": 100}).get("hydra:member", []):
        if b.get("name") == API_KEY_NAME:
            client.delete(f"/api/3/api_keys/{b['uuid']}")
            print(f"  deleted api key {b['uuid']}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=os.getenv("FSR_BASE_URL"), help="appliance host or base URL")
    p.add_argument("--user", default=os.getenv("FSR_USERNAME"))
    p.add_argument("--password", default=os.getenv("FSR_PASSWORD"))
    p.add_argument("--indicator", default="198.51.100.24", help="IP to add to the feed")
    p.add_argument("--role", action="append", default=None, help="role for the api key (repeatable)")
    p.add_argument("--team", action="append", default=None, help="team for the api key (repeatable)")
    p.add_argument("--source", default="FortiSOAR - pyfsr TAXII example", help="source stamp (used by --cleanup)")
    p.add_argument("--no-expiry", action="store_true", help="drop the rolling 24h createDate filter")
    p.add_argument("--cleanup", action="store_true", help="delete what this example created, then exit")
    p.add_argument(
        "--no-verify-ssl",
        action="store_true",
        help="skip TLS verification (lab appliances with self-signed certs; prefer trusting the CA)",
    )
    a = p.parse_args()

    if not (a.host and a.user and a.password):
        p.error("need --host/--user/--password (or FSR_BASE_URL/FSR_USERNAME/FSR_PASSWORD)")

    verify = not a.no_verify_ssl
    base_url = a.host if a.host.startswith("http") else f"https://{a.host}"
    client = FortiSOAR(base_url, username=a.user, password=a.password, verify_ssl=verify)

    if a.cleanup:
        print("Cleanup:")
        cleanup(client, source=a.source)
        return 0

    print("1. TAXII server")
    enable_taxii(client)

    print("2. API key")
    binding, key = ensure_api_key(
        client,
        roles=a.role or ["Full App Permissions"],
        teams=a.team or ["SOC Team"],
    )

    print("3. Dataset (== TAXII collection)")
    ds = create_dataset(client, expiry=not a.no_expiry)

    print("4. Add indicator to the feed")
    add_indicator(client, a.indicator, source=a.source)

    print("5. Query the dataset back (pyfsr)")
    query_dataset(client, ds["uuid"])

    if key:
        print("6. Fetch as a FortiGate would (HTTP Basic)")
        env = fetch_as_fortigate(base_url, ds["uuid"], API_KEY_NAME, key, verify_ssl=verify)
        values = [o.get("value") for o in env.get("objects") or []]
        print(f"  totalItems={env.get('totalItems')}  $.objects[*].value -> {values}")
        print_fortigate_config(base_url, ds["uuid"], API_KEY_NAME, key)
    else:
        print("6. Skipped FortiGate fetch — no plaintext key this run (key already existed).")
        print("   Re-run with --cleanup then again, to mint a fresh key.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
