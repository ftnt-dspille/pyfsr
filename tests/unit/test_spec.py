"""Unit tests for the bundled OpenAPI spec loader + drift check."""

from pyfsr import spec as spec_mod
from pyfsr.spec import drift, load_spec, spec_paths, spec_schemas, spec_version


def test_load_spec_is_openapi_doc():
    doc = load_spec()
    assert doc.get("openapi", "").startswith("3.")
    assert "paths" in doc and "components" in doc


def test_load_spec_cached():
    assert load_spec() is load_spec()  # lru_cache returns the same object


def test_spec_schemas_include_core_entities():
    schemas = spec_schemas()
    assert "Alert" in schemas
    assert schemas == sorted(schemas)


def test_spec_paths_nonempty_sorted():
    paths = spec_paths()
    assert paths and paths == sorted(paths)


def test_spec_version_present():
    assert spec_version() is not None


class FakeClient:
    def list_modules(self, refresh=False):
        # 'alert' overlaps a spec schema; 'custom_module' is live-only.
        return [
            {"type": "alert"},
            {"type": "custom_module"},
            {"type": ""},  # ignored
        ]


def test_drift_partitions_names(monkeypatch):
    monkeypatch.setattr(spec_mod, "spec_schemas", lambda: ["Alert", "Incident"])
    report = drift(FakeClient())
    assert report["common"] == ["alert"]
    assert report["spec_only"] == ["incident"]
    assert report["live_only"] == ["custom_module"]
