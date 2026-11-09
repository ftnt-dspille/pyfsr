"""Seamless compile cache: one file per instance, incremental warm, stale retry."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pyfsr import authoring


def _client(url: str) -> SimpleNamespace:
    return SimpleNamespace(base_url=url)


def test_cache_is_per_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    a = authoring._default_cache_db(_client("https://a.example.com"))
    b = authoring._default_cache_db(_client("https://b.example.com"))
    assert a != b and a.parent == b.parent == tmp_path / "pyfsr"
    assert authoring._default_cache_db(_client("https://a.example.com")) == a


@pytest.mark.parametrize(("raw", "want"), [(None, 300.0), ("60", 60.0), ("0", None), ("junk", 300.0)])
def test_max_age_env(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv("PYFSR_CATALOG_MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("PYFSR_CATALOG_MAX_AGE", raw)
    assert authoring._catalog_max_age() == want


def _fake_compiler(monkeypatch, results):
    calls = []

    def compile_yaml(text, db, lax_codes=None):
        calls.append(db)
        ok = results.pop(0)
        return SimpleNamespace(ok=ok, errors=[], fsr_json={} if ok else None)

    monkeypatch.setattr(authoring, "_load_compiler", lambda: (compile_yaml, lambda: "slim.db"))
    return calls


def test_failed_compile_on_skipped_cache_rewarms_fully_and_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(authoring, "_library_box_catalog", lambda: None)
    warms = []

    def warm(client, db, *, max_age=None, connectors=True):
        warms.append(max_age)
        return {"teams": 1, "teams_skipped": 1} if max_age else {"teams": 1}

    monkeypatch.setattr(authoring, "warm_catalog", warm)
    calls = _fake_compiler(monkeypatch, [False, True])
    res = authoring.compile_playbook_yaml("x", client=_client("https://a.example.com"))
    assert res.ok and len(calls) == 2
    assert warms == [300.0, None]  # incremental, then a full re-pull


def test_failed_compile_after_full_warm_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(authoring, "_library_box_catalog", lambda: None)
    monkeypatch.setattr(authoring, "warm_catalog", lambda *a, **k: {"teams": 1})
    calls = _fake_compiler(monkeypatch, [False])
    assert not authoring.compile_playbook_yaml("x", client=_client("https://a.example.com")).ok
    assert len(calls) == 1


def test_library_warm_gets_the_playbook_then_a_full_warm_on_failure(tmp_path, monkeypatch):
    warms = []

    def box_catalog(client, *, playbook=None, full=False):
        warms.append((playbook, full))
        return tmp_path / "catalog.db"

    monkeypatch.setattr(authoring, "_library_box_catalog", lambda: box_catalog)
    calls = _fake_compiler(monkeypatch, [False, True])
    res = authoring.compile_playbook_yaml("steps: []", client=_client("https://a.example.com"))
    assert res.ok and calls == [tmp_path / "catalog.db"] * 2
    assert warms == [("steps: []", False), (None, True)]


def test_a_failed_library_warm_falls_back_to_the_section_warm(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def box_catalog(client, **kw):
        raise RuntimeError("box unreachable")

    fallback = []
    monkeypatch.setattr(authoring, "_library_box_catalog", lambda: box_catalog)
    monkeypatch.setattr(authoring, "warm_catalog", lambda *a, **k: fallback.append(1) or {"teams": 1})
    _fake_compiler(monkeypatch, [True])
    assert authoring.compile_playbook_yaml("x", client=_client("https://a.example.com")).ok
    assert fallback == [1]
