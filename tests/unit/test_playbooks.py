"""Unit tests for PlaybooksAPI (run history / get / resume)."""

import pytest

from pyfsr.api.playbooks import PlaybooksAPI, _shape_run


def _run(iri, name, status, modified, **extra):
    return {"@id": iri, "name": name, "status": status, "modified": modified, **extra}


class FakeClient:
    def __init__(self, *, workflows=None, historical=None, name_lookup=None, get_raiser=None):
        self.get_calls = []
        self.post_calls = []
        self._workflows = workflows or []
        self._historical = historical or []
        self._name_lookup = name_lookup
        self._get_raiser = get_raiser

    def get(self, endpoint, params=None, **kwargs):
        self.get_calls.append((endpoint, params))
        if self._get_raiser:
            self._get_raiser(endpoint)
        if endpoint.startswith("/api/3/workflows?"):
            return self._name_lookup or {"hydra:member": []}
        if endpoint.startswith("/api/wf/api/historical-workflows/"):
            tail = endpoint.split("/api/wf/api/historical-workflows/")[1]
            if not tail.startswith("?"):  # single get: "<pk>/?format=json"
                seg = tail.split("/")[0]
                return next(
                    (r for r in self._historical if r["@id"].rstrip("/").endswith(seg)),
                    {},
                )
            return {"hydra:member": self._historical}
        if endpoint.startswith("/api/wf/api/workflows/"):
            tail = endpoint.split("/api/wf/api/workflows/")[1]
            if not tail.startswith("?"):  # single get: "<pk>/?format=json"
                seg = tail.split("/")[0]
                return next(
                    (r for r in self._workflows if r["@id"].rstrip("/").endswith(seg)),
                    {},
                )
            return {"hydra:member": self._workflows}
        return {}

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.post_calls.append((endpoint, data))
        return {"resumed": True}


# -- _shape_run -------------------------------------------------------------
def test_shape_run_extracts_pk_and_error():
    m = _run(
        "/api/wf/api/workflows/abc-123/",
        "Block IP",
        "failed",
        "2026-06-08T00:00:00",
        task_id="t1",
        uuid="u1",
        result={"Error message": "boom"},
        _source="live",
    )
    s = _shape_run(m)
    assert s["pk"] == "abc-123"
    assert s["error_message"] == "boom"
    assert s["status"] == "failed"
    assert s["source"] == "live"


def test_shape_run_no_result():
    s = _shape_run(_run("/api/wf/api/workflows/x/", "n", "finished", "t"))
    assert s["error_message"] is None
    assert s["pk"] == "x"


# -- runs -------------------------------------------------------------------
def test_runs_merges_and_dedupes():
    shared = _run("/api/wf/api/workflows/dup/", "Dup", "finished", "2026-06-08T02:00")
    client = FakeClient(
        workflows=[_run("/api/wf/api/workflows/a/", "A", "failed", "2026-06-08T03:00"), shared],
        historical=[
            shared,  # same IRI in both tables -> dedup
            _run("/api/wf/api/historical-workflows/b/", "B", "finished", "2026-06-08T01:00"),
        ],
    )
    runs = PlaybooksAPI(client).runs(limit=10)
    pks = [r["pk"] for r in runs]
    assert pks == ["a", "dup", "b"]  # sorted by modified desc, deduped
    assert {r["source"] for r in runs} == {"live", "historical"}


def test_runs_respects_limit():
    wf = [_run(f"/api/wf/api/workflows/{i}/", f"n{i}", "finished", f"t{i}") for i in range(5)]
    runs = PlaybooksAPI(FakeClient(workflows=wf)).runs(limit=2)
    assert len(runs) == 2


def test_runs_by_playbook_name_resolves_uuid():
    client = FakeClient(
        workflows=[_run("/api/wf/api/workflows/a/", "A", "failed", "t")],
        name_lookup={"hydra:member": [{"uuid": "pb-uuid"}]},
    )
    PlaybooksAPI(client).runs(playbook="Block IP")
    # name lookup happened, and the run fetch carried the template_iri filter
    assert any("/api/3/workflows?" in c[0] for c in client.get_calls)
    assert any("template_iri=/api/3/workflows/pb-uuid" in c[0] for c in client.get_calls)


def test_runs_unknown_playbook_returns_empty():
    client = FakeClient(name_lookup={"hydra:member": []})
    assert PlaybooksAPI(client).runs(playbook="nope") == []


def test_runs_raw_returns_unshaped():
    client = FakeClient(workflows=[_run("/api/wf/api/workflows/a/", "A", "failed", "t")])
    runs = PlaybooksAPI(client).runs(raw=True)
    assert "@id" in runs[0]


# -- get --------------------------------------------------------------------
def test_get_live_then_historical_fallback():
    client = FakeClient(
        workflows=[],
        historical=[_run("/api/wf/api/historical-workflows/h1/", "H", "failed", "t", uuid="u")],
    )
    run = PlaybooksAPI(client).get("h1")
    assert run["pk"] == "h1"
    assert run["source"] == "historical"


def test_get_blank_pk_raises():
    with pytest.raises(ValueError):
        PlaybooksAPI(FakeClient()).get("")


# -- resume -----------------------------------------------------------------
def test_resume_posts_to_wfinput_resume():
    client = FakeClient()
    PlaybooksAPI(client).resume(
        "run-1", manual_input_id=7, input={"choice": "yes"}, step_id="s1", approved=True
    )
    endpoint, body = client.post_calls[0]
    assert endpoint == "/api/wf/api/workflows/run-1/wfinput_resume/?format=json"
    assert body["manual_input_id"] == 7
    assert body["input"] == {"choice": "yes"}
    assert body["approved"] is True


def test_resume_omits_approved_when_none():
    client = FakeClient()
    PlaybooksAPI(client).resume("run-1", manual_input_id=1)
    _, body = client.post_calls[0]
    assert "approved" not in body


def test_resume_blank_pk_raises():
    with pytest.raises(ValueError):
        PlaybooksAPI(FakeClient()).resume("", manual_input_id=1)
