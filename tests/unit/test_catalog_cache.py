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
    monkeypatch.setattr(authoring, "warm_catalog", lambda *a, **k: {"teams": 1})
    calls = _fake_compiler(monkeypatch, [False])
    assert not authoring.compile_playbook_yaml("x", client=_client("https://a.example.com")).ok
    assert len(calls) == 1
