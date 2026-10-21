"""Shared helpers for resolving people records (resume attribution)."""

from __future__ import annotations

from typing import Any


def resolve_user_iri(client: Any) -> str:
    """A ``/api/3/people`` IRI to attribute a manual-input / resume action to.

    Prefers a person whose display name contains "admin" (the same recipe the
    FortiSOAR UI's service flows use), else the first people record. The
    ``wfinput_resume`` route 500s on 8.0.0 when the body carries no ``user``,
    so callers that cannot name a user should let this pick one.

    Raises:
        LookupError: the appliance has no people records to attribute to.
    """
    resp = client.get("/api/3/people", params={"$limit": 50})
    members = resp.get("hydra:member", []) if isinstance(resp, dict) else []
    if not members:
        raise LookupError("no people records found to attribute the action to")
    for m in members:
        name = f"{m.get('firstname') or ''} {m.get('lastname') or ''}".lower()
        if "admin" in name:
            return m["@id"]
    return members[0]["@id"]
