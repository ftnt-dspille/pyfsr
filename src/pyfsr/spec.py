"""Access to the bundled FortiSOAR OpenAPI specification.

pyfsr ships a curated copy of the FortiSOAR OpenAPI spec (the same one the typed
models are derived from) as a gzipped JSON resource. It is offline reference
material — useful for inspecting paths/schemas without a live box, and for
detecting drift between the spec and what a given appliance actually exposes.

Stored as gzipped JSON so the loader needs only the standard library (no PyYAML
runtime dependency)::

    from pyfsr.spec import load_spec, spec_schemas, drift
    spec = load_spec()                 # the full OpenAPI document (dict)
    schemas = spec_schemas()           # ['Alert', 'Incident', 'Task', ...]
    report = drift(client)             # spec vs. live modules
"""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

_RESOURCE = "fortisoar.openapi.json.gz"


@lru_cache(maxsize=1)
def load_spec() -> dict[str, Any]:
    """Return the bundled OpenAPI spec as a dict (decompressed once, cached)."""
    data = (files("pyfsr.resources") / _RESOURCE).read_bytes()
    return json.loads(gzip.decompress(data))


def spec_version() -> str | None:
    """The spec's ``info.version`` (the FortiSOAR API version it describes)."""
    return load_spec().get("info", {}).get("version")


def spec_paths() -> list[str]:
    """Sorted list of API paths documented in the spec."""
    return sorted(load_spec().get("paths", {}))


def spec_schemas() -> list[str]:
    """Sorted list of component schema names (the typed entities) in the spec."""
    return sorted(load_spec().get("components", {}).get("schemas", {}))


def drift(client: Any) -> dict[str, list[str]]:
    """Compare the bundled spec's entities against a live appliance's modules.

    Returns ``{spec_only, live_only, common}`` lists of module/schema names
    (lower-cased for comparison). ``spec_only`` flags entities the spec documents
    that this appliance doesn't expose; ``live_only`` flags modules present on the
    appliance but absent from the bundled spec (likely custom modules, or spec
    drift). Uses ``client.list_modules()``.
    """
    spec_names = {s.lower() for s in spec_schemas()}
    live_names = {str(m.get("type", "")).lower() for m in client.list_modules() if m.get("type")}
    return {
        "spec_only": sorted(spec_names - live_names),
        "live_only": sorted(live_names - spec_names),
        "common": sorted(spec_names & live_names),
    }
