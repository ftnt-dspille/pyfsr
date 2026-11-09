"""Content Hub updates: list what has a newer version, and apply it like the GUI's Update button."""

from __future__ import annotations

import time

import pytest

from pyfsr.api.content_hub import (
    ContentHubSearch,
    ContentUpdate,
    ContentUpdateError,
    _version_tuple,
    order_updates,
)


def _row(name, type_, inst, latest, deps=(), min_fsr=None, label=None):
    return {
        "name": name,
        "label": label or name,
        "type": type_,
        "version": inst,
        "latestAvailableVersion": latest,
        "latestCompatibleVersion": min_fsr,
        "dependencies": [{"name": d} for d in deps] or None,
        "uuid": f"u-{name}",
    }


ROWS = [
    _row("tim", "solutionpack", "3.0.0", "4.0.0", deps=["soarFramework", "vuln"]),
    _row("cardView", "widget", "1.0.0", "2.0.0"),
    _row("vuln", "solutionpack", "2.2.0", "2.3.0", deps=["soarFramework"]),
    _row("servicenow", "connector", "3.5.0", "3.7.0", label="ServiceNow"),
    _row("soarFramework", "solutionpack", "3.4.0", "4.1.0", deps=["platformUtilities"], label="SOAR Framework"),
    _row("stale", "connector", "2.0.0", "2.0.0"),  # catalog says "latest" but it is not newer
]


class _Job:
    def __init__(self, status, errorMessage=None):
        self.status = status
        self.errorMessage = errorMessage


class _SolutionPacks:
    def __init__(self, client):
        self.client = client

    def wait_for_install(self, job_id, *, interval, timeout):
        return _Job(self.client.job_status.get(job_id, "Import Complete"), "boom")


class _Connectors:
    def __init__(self, counts):
        self.counts = counts  # list of counts returned per call
        self.cleared = 0

    def configurations(self, name):
        return [object()] * self.counts.pop(0)

    def clear_cache(self):
        self.cleared += 1


class _Export:
    def export_connector(self, name, output_path=None):
        return output_path or f"{name}-backup.zip"


class _Import:
    def __init__(self):
        self.imported = []

    def import_file(self, path, wait=True):
        self.imported.append(path)


class _Admin:
    def __init__(self, status="Success"):
        self.status = status

    def _publish_status(self):
        return {"status": self.status}


class _Client:
    base_url = "https://box.example"

    def __init__(self, rows=ROWS, *, version="8.0.1-6095", publish="Success", config_counts=(0,)):
        self.rows = list(rows)
        self.posts: list[tuple[str, dict]] = []
        self.job_status: dict[str, str] = {}
        self._version = version
        self.modules_admin = _Admin(publish)
        self.solution_packs = _SolutionPacks(self)
        self.connectors = _Connectors(list(config_counts))
        self.export_config = _Export()
        self.import_config = _Import()
        self.content_hub = ContentHubSearch(self)

    def version(self):
        return self._version

    jobs: list[dict] = []

    def get(self, path, params=None, **kw):
        assert path == "/api/3/import_jobs"
        jobs = self.jobs.pop(0) if self.jobs and isinstance(self.jobs[0], list) else self.jobs
        return {"hydra:member": jobs}

    def post(self, path, data=None, **kw):
        self.posts.append((path, data))
        if path.startswith("/api/query/solutionpacks"):
            types = data["filters"][0]["value"]
            return {"hydra:member": [r for r in self.rows if r["type"] in types]}
        if path == "/api/3/solutionpacks/update":
            # the box brings the item up to date: drop it from later listings
            self.rows = [r for r in self.rows if r["name"] != data["name"]]
            return {"name": data["name"], "importJob": {"uuid": f"job-{data['name']}"}}
        raise AssertionError(path)

    def update_calls(self):
        return [d["name"] for p, d in self.posts if p == "/api/3/solutionpacks/update"]


# ------------------------------------------------------------------ listing
def test_updates_sends_the_guis_update_available_filter():
    c = _Client()
    c.content_hub.updates()
    path, body = c.posts[0]
    f = body["filters"]
    assert f[0] == {"field": "type", "operator": "in", "value": ["solutionpack", "connector", "widget"]}
    assert {"field": "latestAvailableVersion", "operator": "isnull", "value": False} in f
    assert {"field": "installed", "operator": "eq", "value": True} in f
    assert f[3]["logic"] == "OR"  # development connectors excluded, as in the GUI


def test_updates_can_include_development_connectors():
    c = _Client()
    c.content_hub.updates(include_development=True)
    assert len(c.posts[0][1]["filters"]) == 3


def test_updates_drops_rows_that_are_not_newer_and_orders_packs_by_dependency():
    names = [u.name for u in _Client().content_hub.updates()]
    assert "stale" not in names
    assert names == ["soarFramework", "vuln", "tim", "servicenow", "cardView"]


def test_updates_filters_by_type():
    assert [u.name for u in _Client().content_hub.updates("connector")] == ["servicenow"]


def test_version_tuple_orders_properly_unlike_the_gui():
    assert _version_tuple("7.10.0") > _version_tuple("7.9.0")
    assert _version_tuple("8.0.1-6095") > _version_tuple("8.0.1")


def test_order_updates_ignores_dependency_cycles():
    a = ContentUpdate(name="a", type="solutionpack", latest_version="2", depends_on=["b"])
    b = ContentUpdate(name="b", type="solutionpack", latest_version="2", depends_on=["a"])
    assert {u.name for u in order_updates([a, b])} == {"a", "b"}


# ------------------------------------------------------------------ one update
def test_update_by_label_posts_the_guis_update_call_and_waits():
    c = _Client()
    r = c.content_hub.update("SOAR Framework")
    assert c.posts[-1] == ("/api/3/solutionpacks/update", {"name": "soarFramework", "version": "4.1.0"})
    assert r.status == "updated" and r.from_version == "3.4.0" and r.to_version == "4.1.0" and r.ok


def test_update_passes_build_number_and_explicit_version():
    c = _Client()
    c.content_hub.update("servicenow", version="3.6.0", build_number=1001)
    assert c.posts[-1][1] == {"name": "servicenow", "version": "3.6.0", "buildNumber": 1001}


def test_update_without_wait_returns_the_job():
    r = _Client().content_hub.update("cardView", wait=False)
    assert r.status == "started" and r.job_id == "job-cardView"


def test_a_failed_import_job_is_reported():
    c = _Client()
    c.job_status["job-cardView"] = "Import Failed"
    r = c.content_hub.update("cardView")
    assert r.status == "failed" and not r.ok and r.message == "boom"


def test_update_of_something_without_an_update_is_refused_clearly():
    with pytest.raises(ContentUpdateError, match="no update available for 'nope'"):
        _Client().content_hub.update("nope")


def test_update_is_refused_while_a_publish_runs():
    with pytest.raises(ContentUpdateError, match="publish is running"):
        _Client(publish="started").content_hub.update("cardView")
    assert not _Client(publish="started").update_calls()


def test_a_pack_needing_a_newer_fortisoar_is_refused():
    rows = [_row("newPack", "solutionpack", "1.0.0", "2.0.0", min_fsr="8.1.0")]
    c = _Client(rows)
    with pytest.raises(ContentUpdateError, match="needs FortiSOAR 8.1.0 or later; this box runs 8.0.1-6095"):
        c.content_hub.update("newPack")
    assert c.content_hub.update("newPack", check_compatibility=False).status == "updated"


# ------------------------------------------------------------------ connector configs
def test_no_backup_is_taken_and_kept_configurations_are_counted():
    c = _Client(config_counts=(2, 2))
    r = c.content_hub.update("servicenow")
    assert (r.configs_before, r.configs_after) == (2, 2) and r.message is None
    assert not hasattr(r, "backup")


def test_configurations_lost_in_an_update_are_reported_not_restored():
    c = _Client(config_counts=(2, 1))
    r = c.content_hub.update("servicenow")
    assert r.status == "updated" and r.configs_after == 1
    assert r.message == "1 of 2 configurations are gone after the update"


def test_an_unconfigured_connector_reports_zero():
    r = _Client(config_counts=(0,)).content_hub.update("servicenow")
    assert r.configs_before == 0 and r.configs_after is None


# ------------------------------------------------------------------ update_all
def test_update_all_dry_run_sends_nothing():
    c = _Client()
    plan = c.content_hub.update_all(dry_run=True)
    assert [p.status for p in plan] == ["planned"] * 5
    assert c.update_calls() == []


def test_update_all_runs_packs_in_dependency_order_then_the_rest():
    c = _Client()
    results = c.content_hub.update_all()
    assert c.update_calls() == ["soarFramework", "vuln", "tim", "servicenow", "cardView"]
    assert all(r.status == "updated" for r in results)


def test_update_all_skips_what_a_pack_already_updated():
    c = _Client()
    orig = c.post

    def post(path, data=None, **kw):
        out = orig(path, data, **kw)
        if path == "/api/3/solutionpacks/update" and data["name"] == "tim":
            c.rows = [r for r in c.rows if r["name"] != "cardView"]  # the pack bundled the widget
        return out

    c.post = post
    c.content_hub.update_all()
    assert "cardView" not in c.update_calls()


def test_update_all_by_name_and_records_refusals():
    rows = [_row("a", "widget", "1.0", "2.0"), _row("b", "solutionpack", "1.0", "2.0", min_fsr="9.0.0")]
    c = _Client(rows)
    results = c.content_hub.update_all(names=["a", "b"])
    by = {r.name: r for r in results}
    assert by["a"].status == "updated" and by["b"].status == "skipped" and "9.0.0" in by["b"].message


def test_update_all_stop_on_error():
    rows = [_row("p", "solutionpack", "1.0", "2.0", min_fsr="9.0.0"), _row("w", "widget", "1.0", "2.0")]
    c = _Client(rows)
    results = c.content_hub.update_all(stop_on_error=True)
    assert [r.name for r in results] == ["p"] and c.update_calls() == []


# ------------------------------------------------------------------ one install at a time
def _job(status, age=60, what="widgets"):
    return {
        "type": "SolutionPack Import",
        "status": status,
        "modifyDate": time.time() - age,
        "currentlyImporting": what,
    }


def test_running_installs_ignores_finished_parked_and_abandoned_jobs():
    c = _Client()
    c.jobs = [
        _job("Import Complete"),
        _job("Reviewing"),
        _job("In Progress", age=12 * 3600),  # abandoned
        _job("Importing"),
    ]
    assert [j["status"] for j in c.content_hub.running_installs()] == ["Importing"]


def test_update_waits_for_a_running_import_before_starting(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _Client()
    c.jobs = [[_job("Importing")], [_job("Importing")], []]  # busy twice, then idle
    assert c.content_hub.update("cardView").status == "updated"
    assert c.update_calls() == ["cardView"]


def test_update_refuses_while_the_box_stays_busy(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _Client()
    c.jobs = [_job("Importing")]
    with pytest.raises(ContentUpdateError, match="another import is still running"):
        c.content_hub.update("cardView", timeout=0)
    assert c.update_calls() == []


def test_update_all_stops_when_the_box_stays_busy(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    c = _Client()
    c.jobs = [_job("Importing")]
    results = c.content_hub.update_all(timeout=0)
    assert len(results) == 1 and results[0].status == "skipped" and c.update_calls() == []


def test_a_job_still_running_at_timeout_halts_the_run():
    c = _Client()
    c.job_status["job-soarFramework"] = "Importing"
    results = c.content_hub.update_all()
    assert [r.status for r in results] == ["timeout"]
    assert c.update_calls() == ["soarFramework"]  # nothing started on top of it


def test_update_all_rejects_wait_false():
    with pytest.raises(ValueError, match="one import at a time"):
        _Client().content_hub.update_all(wait=False)


def test_connectors_and_widgets_only():
    c = _Client()
    c.content_hub.update_all(["connector", "widget"])
    assert c.update_calls() == ["servicenow", "cardView"]


def test_a_content_service_refusal_is_reported_as_unavailable_and_the_run_continues():
    c = _Client([_row("fai", "widget", "3.0.0", "4.0.0"), _row("w", "widget", "1.0", "2.0")])
    orig = c.post

    def post(path, data=None, **kw):
        if path == "/api/3/solutionpacks/update" and data["name"] == "fai":
            raise RuntimeError("ResourceNotFoundError: Content not available\n")
        return orig(path, data, **kw)

    c.post = post
    results = c.content_hub.update_all()
    assert [(r.name, r.status) for r in results] == [("fai", "unavailable"), ("w", "updated")]
    assert "will not serve this version" in results[0].message
