"""The map router -- classify a use case to an archetype and fill its parameter slots.

The router is the tractable core of the use-case -> FortiSOAR-artifacts mapping (Form-1
plan, section C -- the "no use-case -> artifacts mapping" gap). Given a free-text use case
it:

1. **Classifies** the use case to the best-scoring archetype in
   :class:`~pyfsr.archetypes.store.ArchetypeStore` -- a bounded, deterministic
   keyword/intent match (NOT an LLM call), so the result is reproducible and unit-testable.
2. **Fills** the matched archetype's ``{{param}}`` slots: infers what it can from the use
   case text plus the archetype's own connector manifest and module schema, applies shipped
   defaults, and lists the slots still pending agent/user input.

The router only reads -- it never writes the appliance -- so it is safe to call against any
store, including a ``tmp_path`` store in tests.

Example::

    from pyfsr.archetypes import map_use_case

    result = map_use_case("compare FortiCloud assets vs ServiceNow CMDB, email a CSV")
    result["archetype"]                         # "reconcile-and-report"
    result["parameters"]["source_a_label"]["value"]  # "FortiCloud assets" (inferred)
    result["pending"]                           # ["recipients"]  (still needs a value)
"""

from __future__ import annotations

import re
from typing import Any

from .record import Archetype
from .store import ArchetypeStore

# Function words that carry no use-case signal; dropped before scoring so they neither
# inflate a match (common words coinciding) nor dilute the recall denominator.
_STOPWORDS = frozenset(
    """
    a an the of to in on for with by from and or vs versus as is are be my our we i
    this that these those it its into over per each some any new run running into
    """.split()
)

# Connector -> human label, for inferring ``<role>_label`` parameters. Covers the pilot's
# connectors plus common FortiSOAR ones; an unknown connector falls back to a title-cased
# rendering of its name (dashes/underscores -> spaces), so this only needs entries where the
# fallback would be ugly or wrong (e.g. "smtp" -> "SMTP", not "Smtp").
_CONNECTOR_LABELS: dict[str, str] = {
    "forticloud-asset-management": "FortiCloud assets",
    "servicenow-cmdb": "ServiceNow CMDB",
    "smtp": "SMTP",
    "slack": "Slack",
    "fortinet-fortiflex": "FortiFlex",
    "jira": "Jira",
}

# A use case scores a confident match only at/above this recall threshold; below it the
# router reports no fit and returns the ranked candidates instead, so an agent does not act
# on a near-zero overlap that is really coincidence.
_CONFIDENCE_THRESHOLD = 0.2

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase ``text`` into a set of alnum tokens, minus stopwords + single chars."""
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in _STOPWORDS}


def _archetype_document(archetype: Archetype) -> set[str]:
    """The token set an archetype is scored against: intent surface + connectors + roles.

    Draws from ``when_to_use`` + ``description`` + ``name`` (the intent surface) and every
    connector name + operation + role in the manifest, so a use case that names a connector
    or its role ("source A", "notify") matches the archetype that uses it.
    """
    parts: list[str] = [archetype.when_to_use, archetype.description, archetype.name]
    for use in archetype.connector_manifest:
        parts.extend([use.connector, use.operation, use.role or ""])
    return _tokenize(" ".join(p for p in parts if p))


def _score(use_case_tokens: set[str], doc_tokens: set[str]) -> tuple[float, set[str]]:
    """Recall-style score: fraction of use-case tokens present in the archetype doc.

    Returns ``(confidence, matched)`` where ``matched`` is the intersecting token set (kept
    for the rationale string). An empty use case scores 0.
    """
    if not use_case_tokens:
        return 0.0, set()
    matched = use_case_tokens & doc_tokens
    return len(matched) / len(use_case_tokens), matched


def _role_to_connector(archetype: Archetype) -> dict[str, str]:
    """``{role: connector}`` from the manifest, e.g. ``{"source_a": "forticloud-..."}``."""
    return {use.role: use.connector for use in archetype.connector_manifest if use.role}


def _connector_label(connector: str) -> str:
    """Human label for a connector: known map first, else title-cased name."""
    if connector in _CONNECTOR_LABELS:
        return _CONNECTOR_LABELS[connector]
    return " ".join(word.capitalize() for word in re.split(r"[-_]", connector))


def _infer_join_key(archetype: Archetype) -> str | None:
    """A likely join-key field name from the module schema, or ``None``.

    Prefers a field whose name contains 'join'/'serial'/'key' (the seed's ``reconcile-and-
    report`` schema literally names it ``join_key``). Returns ``None`` when nothing looks
    serial-ish, so the router marks the slot pending instead of guessing.
    """
    for field in archetype.module_schema:
        name = (field.name or "").lower()
        if any(hint in name for hint in ("join", "serial", "key")):
            return field.name
    return None


def _fill_parameters(archetype: Archetype) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fill the archetype's parameter slots, returning ``(parameters, pending)``.

    Each entry is ``{value, source, required, prompt?}`` with ``source`` one of
    ``inferred`` / ``default`` / ``pending``. ``prompt`` (the parameter's description) is
    copied through so an agent knows what to ask for on a pending slot.
    """
    role_conn = _role_to_connector(archetype)
    parameters: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    for param in archetype.parameters:
        name = param.get("name", "")
        origin = param.get("from", "prompt")
        required = bool(param.get("required", False))
        entry: dict[str, Any] = {"value": None, "source": "pending", "required": required}
        prompt = param.get("description")
        if prompt:
            entry["prompt"] = prompt

        if name.endswith("_label"):
            # source_a_label / source_b_label / notify_label -> the connector for that role.
            role = name[: -len("_label")]
            connector = role_conn.get(role)
            if connector:
                entry["value"] = _connector_label(connector)
                entry["source"] = "inferred"
        elif "module_schema" in origin:
            # Inferable from the schema (e.g. join_key); else stays pending.
            inferred = _infer_join_key(archetype)
            if inferred is not None:
                entry["value"] = inferred
                entry["source"] = "inferred"

        # 'use_case' params that aren't <role>_label, and 'prompt' params: apply a shipped
        # default if present, otherwise the slot stays pending.
        if entry["source"] == "pending" and param.get("default") is not None:
            entry["value"] = param["default"]
            entry["source"] = "default"

        if entry["source"] == "pending":
            pending.append(name)
        parameters[name] = entry
    return parameters, pending


def map_use_case(use_case: str, store: ArchetypeStore | None = None) -> dict[str, Any]:
    """Classify a use case to an archetype and fill its parameter slots.

    Reads (never writes) the appliance: ``store`` defaults to the per-user
    :class:`~pyfsr.archetypes.store.ArchetypeStore` (seeded from the shipped
    ``reconcile-and-report`` archetype on first use); pass ``store=ArchetypeStore(db_path=...)``
    for a hermetic test. The classifier is a bounded keyword match, not an LLM call, so the
    result is reproducible.

    Args:
        use_case: free-text description of the operational use case (e.g.
            ``"compare FortiCloud assets vs ServiceNow CMDB, email a CSV on mismatches"``).
        store: archetype store to classify against. Defaults to the shared per-user store,
            seeded from the shipped ``reconcile-and-report`` archetype on first use. Pass an
            explicit store to classify against a custom or hermetic library -- it is used
            as-is and never mutated (so an empty store yields a no-fit, not a re-seed).

    Returns:
        A JSON-safe dict. On a confident match::

            {"archetype": "<name>", "confidence": 0.0-1.0, "rationale": "matched: ...",
             "parameters": {<name>: {"value", "source", "required", "prompt"?}},
             "pending": [<names still needing input>], "notes": "..."}

        ``source`` is ``inferred`` (derived from the use case/archetype), ``default`` (a
        shipped default was applied), or ``pending`` (the agent/user must supply a value).
        On no confident match, returns ``{"archetype": None, "candidates": [{name, score,
        matched}], "notes": "..."}`` so an agent can fall back to manual/harvesting.

    Example::

        from pyfsr.archetypes import map_use_case

        r = map_use_case("compare FortiCloud assets vs ServiceNow CMDB, email a CSV")
        r["archetype"]                              # "reconcile-and-report"
        r["parameters"]["source_a_label"]["value"]  # "FortiCloud assets"
        r["pending"]                                 # ["recipients"]
    """
    # Auto-seed only the default store (out-of-the-box first call loads the shipped
    # archetype). An explicit ``store=`` is caller-managed -- never mutated here -- so a
    # caller can pass an empty store (no fit, no candidates) or a custom library unchanged.
    if store is None:
        store = ArchetypeStore()
        store.seed_if_empty()

    use_case_tokens = _tokenize(use_case)
    ranked: list[tuple[Archetype, float, set[str]]] = []
    for name in store.list():
        archetype = store.get(name)
        if archetype is None:  # pragma: no cover - list()/get() are consistent
            continue
        doc_tokens = _archetype_document(archetype)
        confidence, matched = _score(use_case_tokens, doc_tokens)
        ranked.append((archetype, confidence, matched))
    ranked.sort(key=lambda c: c[1], reverse=True)

    candidates = [
        {"name": a.name, "score": round(score, 4), "matched": sorted(matched)} for a, score, matched in ranked
    ]

    if not ranked or ranked[0][1] < _CONFIDENCE_THRESHOLD:
        return {
            "archetype": None,
            "candidates": candidates,
            "notes": (
                "no archetype confidently matches this use case. Refine the wording, or "
                "harvest/curate a new archetype with harvest_archetype_from_pack and add it "
                "to the store."
            ),
        }

    archetype, confidence, matched = ranked[0]
    parameters, pending = _fill_parameters(archetype)
    return {
        "archetype": archetype.name,
        "confidence": round(confidence, 4),
        "rationale": "matched: " + ", ".join(sorted(matched)) if matched else "weak overlap",
        "parameters": parameters,
        "pending": pending,
        "notes": (
            f"{len(parameters) - len(pending)}/{len(parameters)} parameters filled; "
            f"{len(pending)} pending ({', '.join(pending) or 'none'}). Fill the pending "
            "slots, then use the archetype's module_schema + connector_manifest + "
            "playbook_skeletons to create the module, configure connectors, and push the "
            "playbook."
        ),
    }
