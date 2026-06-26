"""Unit tests for :mod:`pyfsr.publish_probe` — publish blast-radius probing.

All tests run offline with a fake client whose ``request()`` returns canned
status codes on a schedule, simulating a 503 outage window during a publish.
"""

from __future__ import annotations

import threading
import time

from pyfsr.publish_probe import (
    PublishProbe,
    PublishReport,
    SurfaceReport,
    default_surfaces,
)


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeClient:
    """Returns 200 until ``go_down`` is set, then 503 until ``go_up`` is set."""

    def __init__(self) -> None:
        self.down = threading.Event()
        self.recovered = threading.Event()

    def request(self, method, endpoint, raise_on_status=True, **kwargs):
        if self.down.is_set() and not self.recovered.is_set():
            return FakeResponse(503)
        return FakeResponse(200)


class TestSurfaceReport:
    def test_outage_window_detection(self):
        r = SurfaceReport(name="x")
        from datetime import datetime, timezone

        from pyfsr.publish_probe import Sample

        def s(t, down):
            return Sample(
                t_monotonic=t,
                timestamp=datetime.now(timezone.utc),
                status=503 if down else 200,
                latency_seconds=0.0,
                down=down,
            )

        for sample in [s(0, False), s(1, True), s(2, True), s(3, False), s(4, False)]:
            r._record(sample)

        assert r.first_down_monotonic == 1
        assert r.first_recovery_monotonic == 3
        assert r.outage_seconds == 2
        assert r.down_count == 2
        assert r.status_histogram == {"200": 3, "503": 2}

    def test_4xx_after_healthy_baseline_counts_as_down(self):
        """A surface healthy at baseline that later 405s during publish is *down*
        (the POST /api/query case: proxy rejects POST while upstream is down)."""
        r = SurfaceReport(name="query")
        from datetime import datetime, timezone

        from pyfsr.publish_probe import Sample

        def s(t, status):
            return Sample(t, datetime.now(timezone.utc), status, 0.0, down=False)

        for sample in [s(0, 200), s(1, 405), s(2, 405), s(3, 200)]:
            r._record(sample)

        assert r.baseline_status == 200
        assert r.down_count == 2  # the two 405s
        assert r.outage_seconds == 2

    def test_4xx_baseline_is_not_flagged(self):
        """A surface already 4xx at baseline (misconfigured path / permission) is not
        flagged as an outage on that same class."""
        r = SurfaceReport(name="bad_path")
        from datetime import datetime, timezone

        from pyfsr.publish_probe import Sample

        for t, status in [(0, 404), (1, 404), (2, 404)]:
            r._record(Sample(t, datetime.now(timezone.utc), status, 0.0, down=False))
        assert r.baseline_status == 404
        assert r.down_count == 0

    def test_never_recovered_outage_is_none(self):
        r = SurfaceReport(name="x")
        from datetime import datetime, timezone

        from pyfsr.publish_probe import Sample

        r._record(Sample(0, datetime.now(timezone.utc), 503, 0.0, True))
        assert r.first_down_monotonic == 0
        assert r.outage_seconds is None  # no recovery yet


class TestPublishProbe:
    def test_detects_outage_during_publish(self):
        client = FakeClient()
        surfaces = {"modules_meta": lambda: client.request("GET", "/api/3/modules").status_code}
        probe = PublishProbe(client, surfaces, interval=0.02)

        def publish():
            client.down.set()
            time.sleep(0.2)
            client.recovered.set()

        report = probe.run(publish, tail=0.2, warmup=0.1)

        assert isinstance(report, PublishReport)
        assert report.any_outage is True
        r = report.surfaces["modules_meta"]
        assert r.down_count > 0
        assert r.outage_seconds is not None
        # baseline (warmup) saw healthy samples, recovery (tail) too
        assert r.status_histogram.get("200", 0) > 0
        assert r.status_histogram.get("503", 0) > 0

    def test_no_outage_when_publish_is_quiet(self):
        client = FakeClient()  # never goes down
        surfaces = {"record_list": lambda: client.request("GET", "/api/3/alerts").status_code}
        probe = PublishProbe(client, surfaces, interval=0.02)
        report = probe.run(lambda: time.sleep(0.1), tail=0.1, warmup=0.05)
        assert report.any_outage is False
        assert report.surfaces["record_list"].outage_seconds is None

    def test_publish_error_is_captured(self):
        client = FakeClient()
        probe = PublishProbe(client, {"x": lambda: 200}, interval=0.02)

        def boom():
            raise RuntimeError("publish rejected")

        report = probe.run(boom, tail=0.05, warmup=0.02)
        assert "publish rejected" in report.publish_error

    def test_connection_error_counts_as_down(self):
        def flaky():
            raise ConnectionError("dropped")

        probe = PublishProbe(FakeClient(), {"x": flaky}, interval=0.02)
        report = probe.run(lambda: time.sleep(0.08), tail=0.02, warmup=0.02)
        r = report.surfaces["x"]
        assert r.down_count == r.total
        assert r.status_histogram == {"ERR": r.total}

    def test_default_surfaces_shape(self):
        client = FakeClient()
        surfaces = default_surfaces(client, module="incidents")
        assert "modules_meta" in surfaces
        assert "query" in surfaces
        # each is callable and returns a status code against the fake client
        assert surfaces["record_list"]() == 200

    def test_summary_renders(self):
        client = FakeClient()
        probe = PublishProbe(client, {"x": lambda: 200}, interval=0.02)
        report = probe.run(lambda: time.sleep(0.05), tail=0.02, warmup=0.02)
        text = report.summary()
        assert "publish:" in text
        assert "surface" in text
