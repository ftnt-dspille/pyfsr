"""Unit tests for ImportConfigAPI — the config-import lifecycle.

Exercises the four-step job lifecycle (create → generate/wait options → trigger →
wait) and the ``connectors_only`` option mutator without touching a live box, via
a lightweight fake client (the repo convention from ``test_content_hub.py``).
"""

from types import SimpleNamespace

import pytest

from pyfsr.api.import_config import ImportConfigAPI, _job_uuid, connectors_only


class FakeClient:
    """Records calls and dispatches to a per-test handler(method, url, **kw)."""

    def __init__(self, handler=None, upload=None):
        self.calls = []
        self._handler = handler or (lambda *a, **k: {})
        self.files = SimpleNamespace(upload=upload or (lambda path: {"@id": "/api/3/files/f1"}))

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append(("GET", url, params))
        return self._handler("GET", url, params=params)

    def post(self, url, data=None, **kw):
        self.calls.append(("POST", url, data))
        return self._handler("POST", url, data=data)

    def put(self, url, data=None, **kw):
        self.calls.append(("PUT", url, data))
        return self._handler("PUT", url, data=data)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("pyfsr.api.import_config.time.sleep", lambda *_: None)


def _api(handler=None, upload=None):
    c = FakeClient(handler, upload)
    return ImportConfigAPI(c), c


# --------------------------------------------------------------------------- _job_uuid


def test_job_uuid_prefers_uuid_field():
    assert _job_uuid({"uuid": "abc", "@id": "/api/3/import_jobs/zzz"}) == "abc"


def test_job_uuid_falls_back_to_iri_tail():
    assert _job_uuid({"@id": "/api/3/import_jobs/job-9/"}) == "job-9"


def test_job_uuid_none_when_absent():
    assert _job_uuid({"status": "InProgress"}) is None
    assert _job_uuid("not-a-dict") is None


# --------------------------------------------------------------------------- create_job


def test_create_job_posts_inprogress_and_returns_uuid():
    api, c = _api(lambda m, u, **k: {"uuid": "job-1"})
    assert api.create_job("/api/3/files/f1") == "job-1"
    method, url, data = c.calls[-1]
    assert (method, url) == ("POST", "/api/3/import_jobs")
    assert data == {"status": "InProgress", "file": "/api/3/files/f1"}


def test_create_job_raises_without_uuid():
    api, _ = _api(lambda m, u, **k: {"status": "InProgress"})
    with pytest.raises(ValueError, match="could not determine import-job uuid"):
        api.create_job("/api/3/files/f1")


# --------------------------------------------------------------------- options lifecycle


def test_generate_options_hits_async_endpoint():
    api, c = _api(lambda m, u, **k: {"log": "walking bundle"})
    api.generate_options("job-1")
    assert c.calls[-1] == ("GET", "/api/import/job-1", None)


def test_wait_for_options_returns_when_populated():
    opts = {"connectors": {"include": True}}
    api, _ = _api(lambda m, u, **k: {"options": opts})
    assert api.wait_for_options("job-1", interval=0.0, timeout=1.0) == opts


def test_wait_for_options_times_out_when_never_ready():
    api, _ = _api(lambda m, u, **k: {"options": {}})
    with pytest.raises(TimeoutError, match="options not ready"):
        api.wait_for_options("job-1", interval=0.0, timeout=0.0)


def test_set_options_puts_options_body():
    api, c = _api(lambda m, u, **k: {})
    api.set_options("job-1", {"connectors": {"include": True}})
    method, url, data = c.calls[-1]
    assert (method, url) == ("PUT", "/api/3/import_jobs/job-1")
    assert data == {"options": {"connectors": {"include": True}}}


# ------------------------------------------------------------------------ wait_for_import


def test_wait_for_import_returns_on_terminal_status():
    api, _ = _api(lambda m, u, **k: {"status": "Import Complete"})
    job = api.wait_for_import("job-1", interval=0.0, timeout=1.0)
    assert job.status == "Import Complete"


def test_wait_for_import_returns_last_poll_on_timeout():
    api, _ = _api(lambda m, u, **k: {"status": "Reviewing"})
    job = api.wait_for_import("job-1", interval=0.0, timeout=0.0)
    assert job.status == "Reviewing"  # non-terminal, returned rather than raised


def test_trigger_starts_run():
    api, c = _api(lambda m, u, **k: {})
    api.trigger("job-1")
    assert c.calls[-1] == ("PUT", "/api/import/job-1", None)


# --------------------------------------------------------------------------- connectors_only


def test_connectors_only_forces_config_restore_and_disables_others():
    options = {
        "connectors": {
            "include": False,
            "values": [{"includeConfigurations": False, "includeInstall": True}],
        },
        "modules": {"include": True},
        "playbooks": {"include": True},
    }
    out = connectors_only(options)
    assert out["connectors"]["include"] is True
    entry = out["connectors"]["values"][0]
    assert entry["includeConfigurations"] is True
    assert entry["includeInstall"] is False
    assert out["modules"]["include"] is False
    assert out["playbooks"]["include"] is False


# --------------------------------------------------------------------------- import_file


def _lifecycle_handler():
    """Job is ready with options and already complete on every poll."""

    def handler(method, url, **kw):
        if method == "POST" and url == "/api/3/import_jobs":
            return {"uuid": "job-1"}
        if method == "GET" and url.startswith("/api/3/import_jobs/"):
            return {
                "uuid": "job-1",
                "status": "Import Complete",
                "options": {"connectors": {"include": True}},
            }
        return {}

    return handler


def test_import_file_end_to_end_stashes_job_uuid():
    api, c = _api(_lifecycle_handler())
    final = api.import_file("backup.zip", interval=0.0, timeout=1.0, options_timeout=1.0)
    assert final.status == "Import Complete"
    assert final["jobUuid"] == "job-1"
    # upload → create job → generate options → trigger all happened
    posted = [(m, u) for m, u, _ in c.calls]
    assert ("POST", "/api/3/import_jobs") in posted
    assert ("PUT", "/api/import/job-1") in posted


def test_import_file_applies_modify_options_before_trigger():
    api, c = _api(_lifecycle_handler())
    api.import_file(
        "backup.zip",
        modify_options=connectors_only,
        interval=0.0,
        timeout=1.0,
        options_timeout=1.0,
    )
    puts = [(u, d) for m, u, d in c.calls if m == "PUT" and u == "/api/3/import_jobs/job-1"]
    assert puts, "expected a PUT to set tweaked options"
    assert puts[0][1]["options"]["connectors"]["include"] is True


def test_import_file_no_wait_returns_after_trigger():
    api, c = _api(_lifecycle_handler())
    job = api.import_file("backup.zip", wait=False, options_timeout=1.0)
    assert job["jobUuid"] == "job-1"
    assert c.calls[-1][:2] == ("GET", "/api/3/import_jobs/job-1")  # final get_job, not a poll loop


def test_import_file_raises_when_upload_returns_no_iri():
    api, _ = _api(_lifecycle_handler(), upload=lambda path: {})
    with pytest.raises(ValueError, match="file upload returned no @id"):
        api.import_file("backup.zip", options_timeout=1.0)
