"""Coverage for authoring.py pure helpers: diagnostic formatting, lax-code
normalization, cache-db resolution, and catalog resolution."""

from pathlib import Path

from pyfsr.authoring import (
    _default_cache_db,
    _normalize_lax_codes,
    _resolve_catalog,
    format_diagnostic,
)


# -- format_diagnostic -------------------------------------------------------
def test_format_diagnostic_minimal():
    line = format_diagnostic({"code": "X", "message": "boom"})
    assert line == "[ERROR] X: boom"


def test_format_diagnostic_with_path():
    line = format_diagnostic({"severity": "warning", "code": "Y", "path": "steps[0]", "message": "m"})
    assert line == "[WARNING] Y at steps[0]: m"


def test_format_diagnostic_with_suggestion_and_near():
    line = format_diagnostic({"code": "Z", "message": "bad", "suggestion": "try this", "near": "foo"})
    assert "(suggestion: try this)" in line
    assert "(near: foo)" in line


# -- _normalize_lax_codes ----------------------------------------------------
def test_normalize_lax_codes_none_and_empty():
    assert _normalize_lax_codes(None) is None
    assert _normalize_lax_codes([]) is None


def test_normalize_lax_codes_passes_through_unknown_strings():
    out = _normalize_lax_codes(["definitely_not_a_real_code"])
    assert out is not None
    # unknown string survives verbatim in the resulting set
    assert "definitely_not_a_real_code" in {str(c) for c in out}


def test_normalize_lax_codes_resolves_known_value():
    # accepts both the friendly value and the enum NAME form for the same code
    from pyfsr.authoring import _normalize_lax_codes as norm

    by_value = norm(["unknown_param"])
    by_name = norm(["UNKNOWN_PARAM"])
    assert by_value == by_name


# -- _default_cache_db -------------------------------------------------------
def test_default_cache_db_honors_xdg(monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdgcache")
    p = _default_cache_db()
    assert p == Path("/tmp/xdgcache/pyfsr/fsr_reference.db")


def test_default_cache_db_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    p = _default_cache_db()
    assert p.parts[-3:] == (".cache", "pyfsr", "fsr_reference.db")


# -- _resolve_catalog --------------------------------------------------------
def test_resolve_catalog_explicit_db_path_wins():
    p = _resolve_catalog(client=None, db_path="/some/catalog.db")
    assert p == Path("/some/catalog.db")


def test_resolve_catalog_no_client_uses_packaged_default():
    p = _resolve_catalog(client=None, db_path=None)
    # packaged slim catalog path from the compiler
    assert isinstance(p, Path)
