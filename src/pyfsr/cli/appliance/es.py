"""``pyfsr appliance es`` — Elasticsearch health and shard verbs.

The ES password is the device UUID (user ``elastic``), resolved by
:class:`.facts.Facts` so it's never re-derived per-session.
"""

from __future__ import annotations

from pydantic import BaseModel

from .facts import Facts


class ESHealth(BaseModel):
    """Parsed output of the ES cluster-health API."""

    status: str  # green / yellow / red
    cluster_name: str
    num_nodes: int
    num_data_nodes: int
    active_shards: int
    unassigned_shards: int
    raw: str


def _curl_es(facts: Facts, path: str, *, timeout: float = 15.0) -> str:
    """Run a curl GET against the local ES REST API using the elastic credentials."""
    uuid = facts.device_uuid()
    res = facts.transport.run(
        [
            "curl",
            "-sk",
            "--max-time",
            str(int(timeout)),
            "-u",
            f"elastic:{uuid}",
            f"http://localhost:9200{path}",
        ],
        timeout=timeout + 5,
    )
    return res.stdout.strip()


def health(facts: Facts) -> ESHealth:
    """Cluster health (``GET /_cluster/health``)."""
    import json

    raw = _curl_es(facts, "/_cluster/health")
    try:
        d = json.loads(raw)
        if not isinstance(d, dict):
            raise ValueError("expected object")
    except Exception:
        return ESHealth(
            status="unknown",
            cluster_name="",
            num_nodes=0,
            num_data_nodes=0,
            active_shards=0,
            unassigned_shards=0,
            raw=raw,
        )
    return ESHealth(
        status=d.get("status", "unknown"),
        cluster_name=d.get("cluster_name", ""),
        num_nodes=d.get("number_of_nodes", 0),
        num_data_nodes=d.get("number_of_data_nodes", 0),
        active_shards=d.get("active_shards", 0),
        unassigned_shards=d.get("unassigned_shards", 0),
        raw=raw,
    )


def shards(facts: Facts) -> tuple[list[str], list[list[str]]]:
    """Unassigned-shard explain (``GET /_cluster/allocation/explain``).

    Returns ``(headers, rows)``. If all shards are assigned, returns an empty
    row list (not an error).
    """
    import json

    raw = _curl_es(facts, "/_cluster/allocation/explain")
    try:
        d = json.loads(raw)
    except Exception:
        return ["info"], [[raw or "(no response)"]]

    # ES returns {"error": ...} when there are no unassigned shards.
    if "error" in d:
        msg = d["error"].get("reason", str(d["error"]))
        if "no unassigned" in msg.lower() or "no shard" in msg.lower():
            return ["info"], [["(no unassigned shards)"]]
        return ["info"], [[f"error: {msg}"]]

    shard = d.get("shard", "?")
    index = d.get("index", "?")
    primary = d.get("primary", "?")
    node_decisions = d.get("node_allocation_decisions", [])
    rows = []
    if node_decisions:
        for nd in node_decisions:
            node = nd.get("node_name", "?")
            dec = nd.get("decider_decisions", [{}])
            reason = "; ".join(x.get("explanation", "") for x in dec if x.get("decision") != "YES")
            rows.append([index, str(shard), str(primary), node, reason or "ok"])
    else:
        reason = d.get("allocate_explanation", d.get("explanation", "(see raw)"))
        rows.append([index, str(shard), str(primary), "", reason])
    return ["index", "shard", "primary", "node", "reason"], rows
