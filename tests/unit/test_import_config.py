"""Unit tests for ImportConfigAPI — the config-import lifecycle.

Exercises the four-step job lifecycle (create → generate/wait options → trigger →
wait) and the ``connectors_only`` option mutator without touching a live box, via
a lightweight fake client (the repo convention from ``test_content_hub.py``).
"""

from types import SimpleNamespace

import pytest

from pyfsr.api.import_config import (
    ImportConfigAPI,
    _job_uuid,
    connector_flags,
    connectors_only,
    inspect_changes,
    keep_existing,
    merge_mode,
    overwrite_all,
    skip_schema_changes,
)
from pyfsr.exceptions import FortiSOARException


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


def test_wait_for_import_rides_through_migrate_503s():
    # First poll succeeds (still importing), then the appliance goes into its
    # backup/migrate cycle and throws 503s, then comes back Complete. The 503s
    # must be tolerated, not raised.
    seq = iter(
        [
            {"status": "Reviewing"},
            FortiSOARException("System Backup", response=SimpleNamespace(status_code=503)),
            FortiSOARException("Clearing Cache", response=SimpleNamespace(status_code=503)),
            {"status": "Import Complete"},
        ]
    )

    def handler(m, u, **k):
        nxt = next(seq)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    api, _ = _api(handler)
    job = api.wait_for_import("job-1", interval=0.0, timeout=5.0)
    assert job.status == "Import Complete"


def test_wait_for_import_reraises_non_transient_error():
    def handler(m, u, **k):
        raise FortiSOARException("Bad Request", response=SimpleNamespace(status_code=400))

    api, _ = _api(handler)
    with pytest.raises(FortiSOARException):
        api.wait_for_import("job-1", interval=0.0, timeout=5.0)


def test_wait_until_ready_settles_after_cache_rebuild():
    seq = iter(
        [
            FortiSOARException("Clearing Cache", response=SimpleNamespace(status_code=503)),
            FortiSOARException("Schema Update", response=SimpleNamespace(status_code=503)),
            {"hydra:member": []},
        ]
    )

    def handler(m, u, **k):
        nxt = next(seq)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    api, c = _api(handler)
    assert api.wait_until_ready(interval=0.0, timeout=5.0) is True
    assert c.calls[-1][1] == "/api/3/staging_model_metadatas"


def test_wait_until_ready_returns_false_on_timeout():
    def handler(m, u, **k):
        raise FortiSOARException("System Backup", response=SimpleNamespace(status_code=503))

    api, _ = _api(handler)
    assert api.wait_until_ready(interval=0.0, timeout=0.0) is False


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


# --------------------------------------------------------------------------- connector_flags


def test_connector_flags_sets_both_toggles_and_leaves_others():
    options = {
        "connectors": {
            "include": False,
            "values": [
                {"includeConfigurations": False, "includeInstall": True},
                {"includeConfigurations": False, "includeInstall": True},
            ],
        },
        "modules": {"include": True},
    }
    out = connector_flags(options, include_install=False, include_configurations=True)
    assert out["connectors"]["include"] is True
    for entry in out["connectors"]["values"]:
        assert entry["includeInstall"] is False
        assert entry["includeConfigurations"] is True
    # unlike connectors_only, other sections are untouched
    assert out["modules"]["include"] is True


def test_connector_flags_none_leaves_toggle_untouched():
    options = {
        "connectors": {
            "include": False,
            "values": [{"includeConfigurations": False, "includeInstall": True}],
        }
    }
    out = connector_flags(options, include_install=False)  # only install specified
    entry = out["connectors"]["values"][0]
    assert entry["includeInstall"] is False
    assert entry["includeConfigurations"] is False  # left as-is
    assert out["connectors"]["include"] is True


def test_connector_flags_noop_when_nothing_specified():
    options = {"connectors": {"include": False, "values": [{"includeInstall": True}]}}
    out = connector_flags(options)
    # no toggles given -> include flag not forced on
    assert out["connectors"]["include"] is False


# --------------------------------------------------------------------------- merge_mode


def _merge_options():
    # Mirrors the live-verified generated-options shape (8.0.0): per-category
    # values each carry a whenExists string.
    return {
        "recordSets": {
            "include": True,
            "values": [
                {"type": "alerts", "whenExists": "replace", "moduleNotExists": False},
                {"type": "incidents", "whenExists": "replace", "moduleNotExists": False},
            ],
        },
        "picklistNames": {
            "include": True,
            "values": [{"name": "AlertStatus", "whenExists": "keep", "exists": True}],
        },
    }


def test_merge_mode_sets_record_sets_and_picklists():
    out = merge_mode(_merge_options(), record_sets="append", picklists="overwrite")
    assert [v["whenExists"] for v in out["recordSets"]["values"]] == ["append", "append"]
    assert out["picklistNames"]["values"][0]["whenExists"] == "overwrite"


def test_merge_mode_none_leaves_category_untouched():
    out = merge_mode(_merge_options(), record_sets="append")  # picklists left alone
    assert out["recordSets"]["values"][0]["whenExists"] == "append"
    assert out["picklistNames"]["values"][0]["whenExists"] == "keep"


def test_merge_mode_rejects_bad_record_set_mode():
    with pytest.raises(ValueError, match="record_sets must be one of"):
        merge_mode(_merge_options(), record_sets="clobber")


def test_merge_mode_rejects_bad_picklist_mode():
    with pytest.raises(ValueError, match="picklists must be one of"):
        merge_mode(_merge_options(), picklists="append")


def test_merge_mode_tolerates_missing_category():
    # a bundle with no record sets / picklists -> no error, nothing to set
    out = merge_mode({"modules": {"include": True, "values": []}}, record_sets="append", picklists="keep")
    assert out == {"modules": {"include": True, "values": []}}


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


# ----------------------------------------------------------- module merge options


def _module_options():
    """Generated options mirroring the wizard's module-merge screen (real shape)."""
    return {
        "modules": {
            "include": True,
            "values": [
                {
                    "type": "oguraly_test_processes",
                    "name": "Oguraly Test Processes",
                    "include": True,
                    "exists": True,
                    "_schema": True,
                    "changes": [
                        {
                            "field": "tableName",
                            "message": "tableName changed from oguraly_test_processes to oguraly_test_process",
                        }
                    ],
                    "attributes": [
                        {
                            "name": "playbooktype",
                            "title": "playbook_type",
                            "exists": True,
                            "include": True,
                            "_include": "yes",
                            "inUniqueConstraint": False,
                            "changes": {"playbooktype": [{"field": "searchable", "new": True}]},
                        },
                        {
                            "name": "identifier",
                            "title": "identifier",
                            "exists": True,
                            "include": True,
                            "_include": "yes",
                            "inUniqueConstraint": True,
                            "changes": {"identifier": [{"field": "validation", "new": {}}]},
                        },
                        {
                            "name": "testint",
                            "title": "testint",
                            "exists": True,
                            "include": True,
                            "_include": "yes",
                            "inUniqueConstraint": False,
                            "changes": {"testint": [{"field": "type", "new": "integer"}]},
                        },
                    ],
                }
            ],
        }
    }


def test_inspect_changes_flags_rename_type_and_unique_constraint():
    risks = inspect_changes(_module_options())
    kinds = {r["kind"] for r in risks}
    assert "tableName change" in kinds  # module rename → index collision risk
    assert "field type change" in kinds  # testint text→integer column rewrite
    assert "unique-constraint field change" in kinds  # identifier
    # a benign "searchable" flip on playbooktype is NOT flagged
    assert all(r["field"] != "playbooktype" for r in risks)


def test_inspect_changes_empty_when_safe():
    assert inspect_changes({"modules": {"values": []}}) == []
    assert inspect_changes({"connectors": {"include": True}}) == []


def test_overwrite_all_sets_include_yes():
    opts = _module_options()
    keep_existing(opts)  # first flip everything to keep
    overwrite_all(opts)
    attrs = opts["modules"]["values"][0]["attributes"]
    assert all(a["include"] is True and a["_include"] == "yes" for a in attrs)


def test_keep_existing_targets_named_field_only():
    opts = _module_options()
    keep_existing(opts, ["playbook_id", "identifier"])  # matched on title
    attrs = {a["name"]: a for a in opts["modules"]["values"][0]["attributes"]}
    assert attrs["identifier"]["include"] is False and attrs["identifier"]["_include"] == "no"
    assert attrs["testint"]["include"] is True  # untouched


def test_skip_schema_changes_clears_schema_flag():
    opts = skip_schema_changes(_module_options())
    assert opts["modules"]["values"][0]["_schema"] is False


def _module_lifecycle_handler(status="Import Complete", error=""):
    def handler(method, url, **kw):
        if method == "POST" and url == "/api/3/import_jobs":
            return {"uuid": "job-1"}
        if method == "GET" and url.startswith("/api/3/import_jobs/"):
            return {
                "uuid": "job-1",
                "status": status,
                "errorMessage": error,
                "options": _module_options(),
            }
        return {}

    return handler


def test_import_file_refuses_risky_schema_changes_by_default():
    api, c = _api(_module_lifecycle_handler())
    with pytest.raises(ValueError, match="refusing to import"):
        api.import_file("m.zip", interval=0.0, timeout=1.0, options_timeout=1.0)
    # never triggered the run
    assert ("PUT", "/api/import/job-1") not in [(m, u) for m, u, _ in c.calls]


def test_import_file_resolve_skip_schema_proceeds_and_sets_flag():
    api, c = _api(_module_lifecycle_handler())
    api.import_file("m.zip", resolve="skip_schema", interval=0.0, timeout=1.0, options_timeout=1.0)
    puts = [d for m, u, d in c.calls if m == "PUT" and u == "/api/3/import_jobs/job-1"]
    assert puts and puts[0]["options"]["modules"]["values"][0]["_schema"] is False
    assert ("PUT", "/api/import/job-1") in [(m, u) for m, u, _ in c.calls]


def test_import_file_rejects_unknown_resolve():
    api, _ = _api(_module_lifecycle_handler())
    with pytest.raises(ValueError, match="unknown resolve strategy"):
        api.import_file("m.zip", resolve="bogus", options_timeout=1.0)


def test_import_file_allow_schema_changes_bypasses_refusal():
    api, c = _api(_module_lifecycle_handler())
    api.import_file("m.zip", allow_schema_changes=True, interval=0.0, timeout=1.0, options_timeout=1.0)
    assert ("PUT", "/api/import/job-1") in [(m, u) for m, u, _ in c.calls]


def test_import_file_verify_raises_on_failed_migrate():
    err = (
        "Publish failed with exception: An exception occurred while executing "
        "'CREATE INDEX oguraly_test_processes_modifydate_idx ON oguraly_test_process "
        "(modifydate)': SQLSTATE[42P07]: Duplicate table: 7 ERROR: relation "
        '"oguraly_test_processes_modifydate_idx" already exists'
    )
    api, _ = _api(_module_lifecycle_handler(status="Error", error=err))
    with pytest.raises(FortiSOARException, match="half-applied migration"):
        api.import_file("m.zip", allow_schema_changes=True, interval=0.0, timeout=1.0, options_timeout=1.0)


def test_import_file_verify_false_returns_failed_job():
    api, _ = _api(_module_lifecycle_handler(status="Error", error="boom"))
    job = api.import_file(
        "m.zip",
        allow_schema_changes=True,
        verify=False,
        interval=0.0,
        timeout=1.0,
        options_timeout=1.0,
    )
    assert job.status == "Error"
