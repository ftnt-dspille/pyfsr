"""Measure the *blast radius* of a FortiSOAR publish: which API surfaces break,
and for how long, while a schema change is committed.

Unlike :mod:`pyfsr.loadtest` (which hammers a *single* playbook trigger to find one
outage window), this probe runs a **matrix** of API surfaces concurrently — record
list, query, view resolve, module schema, playbook trigger, … — each in its own
poller thread, while the caller drives a publish in the foreground. For every surface
it records a per-call sample (timestamp, HTTP status, latency) so you can answer:

* Did ``/api/3`` actually go 503, or did it stay up?
* Which surfaces broke, which survived, and for how long each was down?
* How long did the publish itself take, wall-clock?

This is the empirical test behind the FortiSOAR 8.0 "reduced publish impact" claim:
a *minor* staged change (field visibility / required-by-condition) should publish
faster and with a smaller blast radius than a *structural* one (new field / new
module). Run the same probe around each change class and compare the reports.

Classification is **baseline-aware**: each surface establishes a healthy baseline during
the warmup window, then a sample counts as **down** if it is a connection failure, an
HTTP ``5xx``, *or* any ``>=400`` status when the surface was healthy at baseline. This
catches outages that don't surface as 503 — e.g. ``POST /api/query`` returns ``405`` (not
503) during a publish because the proxy rejects the POST while the upstream is down. A
surface that is *already* erroring at baseline (e.g. a 404 path, or a 403 permission) is
never flagged on that same status, so a misconfigured probe can't masquerade as an outage.

Example::

    from pyfsr import FortiSOAR
    from pyfsr.publish_probe import PublishProbe, default_surfaces

    client = FortiSOAR("https://soar.example.com", api_key="...")
    probe = PublishProbe(client, default_surfaces(client))
    report = probe.run(lambda: client.modules_admin.publish())
    print(report.summary())
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import FortiSOAR

#: A surface probe: called with no args, returns an HTTP status code (or None for a
#: non-HTTP success), and raises on a connection-level failure.
SurfaceFn = Callable[[], "int | None"]


@dataclass
class Sample:
    """One poll of one surface.

    Attributes:
        t_monotonic: ``time.monotonic()`` when the call was fired (for ordering/duration).
        timestamp: Wall-clock UTC time of the call (for human-readable reports).
        status: HTTP status code, or None when the call raised before a response.
        latency_seconds: Elapsed time of the call.
        down: True if this sample counts as an outage (5xx or connection failure).
        error: Exception text when the call raised, else None.
    """

    t_monotonic: float
    timestamp: datetime
    status: int | None
    latency_seconds: float
    down: bool
    error: str | None = None


@dataclass
class SurfaceReport:
    """Outage analysis for a single surface across the publish window.

    Attributes:
        name: Surface label.
        samples: Every poll, in order.
        first_down_monotonic: monotonic time of the first down sample, or None.
        first_recovery_monotonic: monotonic time of the first up sample *after* the
            first down, or None (still down / never went down).
    """

    name: str
    samples: list[Sample] = field(default_factory=list)
    first_down_monotonic: float | None = None
    first_recovery_monotonic: float | None = None
    #: The first observed HTTP status, used as the health baseline. A surface that is
    #: already >=400 here is treated as "expected to error" and not flagged on that class.
    baseline_status: int | None = None
    _baseline_set: bool = False

    @property
    def total(self) -> int:
        return len(self.samples)

    @property
    def down_count(self) -> int:
        return sum(1 for s in self.samples if s.down)

    @property
    def outage_seconds(self) -> float | None:
        """Seconds between first down and first recovery, or None if never recovered/down."""
        if self.first_down_monotonic is None:
            return None
        if self.first_recovery_monotonic is None:
            return None
        return self.first_recovery_monotonic - self.first_down_monotonic

    @property
    def status_histogram(self) -> dict[str, int]:
        """Count of outcomes keyed by status code (or ``ERR`` for connection failures)."""
        hist: dict[str, int] = {}
        for s in self.samples:
            key = str(s.status) if s.status is not None else "ERR"
            hist[key] = hist.get(key, 0) + 1
        return hist

    def _classify(self, status: int | None, errored: bool) -> bool:
        """Decide whether a sample is *down*, relative to the surface baseline.

        Down = a connection failure, an HTTP 5xx, or any >=400 status when the surface
        was healthy (<400) at baseline. The first sample sets the baseline and is itself
        only down on a hard failure (conn error / 5xx).
        """
        if not self._baseline_set:
            self._baseline_set = True
            self.baseline_status = status
            return errored or (status is not None and status >= 500)
        if errored or (status is not None and status >= 500):
            return True
        if status is not None and status >= 400:
            # A 4xx counts as down only if the surface was healthy at baseline.
            return self.baseline_status is not None and self.baseline_status < 400
        return False

    def _record(self, sample: Sample) -> None:
        sample.down = self._classify(sample.status, sample.error is not None)
        self.samples.append(sample)
        if sample.down:
            if self.first_down_monotonic is None:
                self.first_down_monotonic = sample.t_monotonic
        else:
            if self.first_down_monotonic is not None and self.first_recovery_monotonic is None:
                self.first_recovery_monotonic = sample.t_monotonic


@dataclass
class PublishReport:
    """Full blast-radius report for one publish.

    Attributes:
        surfaces: Per-surface outage analysis, keyed by name.
        publish_seconds: Wall-clock duration of the publish driver call.
        publish_error: Exception text if the publish driver raised, else None.
    """

    surfaces: dict[str, SurfaceReport]
    publish_seconds: float
    publish_error: str | None = None

    @property
    def any_outage(self) -> bool:
        return any(r.first_down_monotonic is not None for r in self.surfaces.values())

    def summary(self) -> str:
        """Render a compact per-surface table (publish duration + outage per surface)."""
        lines = [
            f"publish: {self.publish_seconds:.1f}s" + (f"  ERROR: {self.publish_error}" if self.publish_error else ""),
            f"{'surface':<22} {'down/total':>11} {'outage(s)':>10}  statuses",
        ]
        for name, r in self.surfaces.items():
            outage = "—" if r.outage_seconds is None else f"{r.outage_seconds:.1f}"
            if r.first_down_monotonic is not None and r.first_recovery_monotonic is None:
                outage += "+"  # went down and never recovered within the window
            hist = " ".join(f"{k}:{v}" for k, v in sorted(r.status_histogram.items()))
            lines.append(f"{name:<22} {f'{r.down_count}/{r.total}':>11} {outage:>10}  {hist}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """JSON-serializable form for saving a result file."""
        return {
            "publish_seconds": self.publish_seconds,
            "publish_error": self.publish_error,
            "any_outage": self.any_outage,
            "surfaces": {
                name: {
                    "total": r.total,
                    "down_count": r.down_count,
                    "outage_seconds": r.outage_seconds,
                    "recovered": r.first_recovery_monotonic is not None,
                    "status_histogram": r.status_histogram,
                }
                for name, r in self.surfaces.items()
            },
        }


def _http_surface(client: FortiSOAR, method: str, endpoint: str, **kwargs) -> SurfaceFn:
    """Build a surface that fires one request and returns its status code.

    Uses ``raise_on_status=False`` so an HTTP error (incl. the publish 503) is reported
    as a status code rather than an exception — only connection-level failures raise.
    """

    def fn() -> int | None:
        resp = client.request(method, endpoint, raise_on_status=False, **kwargs)
        return resp.status_code

    return fn


def default_surfaces(client: FortiSOAR, *, module: str = "alerts") -> dict[str, SurfaceFn]:
    """A read-only matrix of the API surfaces most likely to break during a publish.

    All probes are non-mutating GET/POST-query reads. ``module`` selects the record
    module used for the list/query/view probes (default ``alerts`` — present on every
    appliance). Add a playbook-trigger surface yourself if you want to measure the
    workflow path (it mutates), e.g.::

        surfaces = default_surfaces(client)
        surfaces["playbook_trigger"] = lambda: client.playbooks.trigger("My PB") or 200

    Args:
        client: An initialized FortiSOAR client.
        module: Record module for the list/query/view-resolve probes.

    Returns:
        Mapping of surface name to a zero-arg callable returning an HTTP status code.
    """
    return {
        # The module schema endpoint a publish rebuilds — the prime 503 suspect.
        "modules_meta": _http_surface(client, "GET", "/api/3/modules", params={"$limit": 1}),
        # Plain record read on /api/3.
        "record_list": _http_surface(client, "GET", f"/api/3/{module}", params={"$limit": 1}),
        # The query engine (separate service path).
        "query": _http_surface(client, "POST", f"/api/query/{module}", data={"limit": 1}),
        # View/template resolution (UI render path).
        "view_resolve": _http_surface(client, "GET", f"/api/views/1/modules-{module}-detail"),
        # App config / navigation (the 1290662 surface).
        "app_config": _http_surface(client, "GET", "/api/views/1/app"),
        # Picklists.
        "picklists": _http_surface(client, "GET", "/api/3/picklists", params={"$limit": 1}),
        # Auth/whoami — does the auth layer stay up through a publish?
        "auth_people": _http_surface(client, "GET", "/api/3/people", params={"$limit": 1}),
    }


class PublishProbe:
    """Concurrently poll a matrix of API surfaces while a publish runs.

    Each surface gets its own daemon poller thread looping at ``interval``. The caller
    passes a ``publish_fn`` to :meth:`run`; the probe records samples from the moment it
    starts until ``tail`` seconds after ``publish_fn`` returns (to capture recovery),
    then returns a :class:`PublishReport`.
    """

    def __init__(
        self,
        client: FortiSOAR,
        surfaces: dict[str, SurfaceFn],
        *,
        interval: float = 0.5,
    ) -> None:
        """Initialize the probe.

        Args:
            client: An initialized FortiSOAR client (used only by surface callables).
            surfaces: Mapping of surface name to a zero-arg callable (see
                :func:`default_surfaces`). A callable returning an HTTP status counts a
                5xx as down; raising counts as down (connection failure).
            interval: Seconds between polls within each surface thread.
        """
        self.client = client
        self.surfaces = surfaces
        self.interval = interval
        self._stop = threading.Event()
        self._reports: dict[str, SurfaceReport] = {}

    def _poll_loop(self, name: str, fn: SurfaceFn) -> None:
        report = self._reports[name]
        while not self._stop.is_set():
            start = time.monotonic()
            ts = datetime.now(timezone.utc)
            status: int | None = None
            err: str | None = None
            try:
                status = fn()
            except Exception as exc:  # noqa: BLE001 — any failure is a candidate "down"
                err = f"{type(exc).__name__}: {exc}"
            # _record() classifies down relative to this surface's health baseline.
            report._record(
                Sample(
                    t_monotonic=start,
                    timestamp=ts,
                    status=status,
                    latency_seconds=time.monotonic() - start,
                    down=False,
                    error=err,
                )
            )
            self._stop.wait(self.interval)

    def run(
        self,
        publish_fn: Callable[[], object],
        *,
        tail: float = 5.0,
        warmup: float = 1.0,
    ) -> PublishReport:
        """Run the probe around a publish.

        Starts all surface pollers, waits ``warmup`` seconds to establish a healthy
        baseline, invokes ``publish_fn`` (blocking until it returns or raises), polls a
        further ``tail`` seconds to capture recovery, then stops and returns the report.

        Args:
            publish_fn: A zero-arg callable that triggers and waits for the publish —
                e.g. ``lambda: client.modules_admin.publish()`` or an import-config call.
                Its return value is ignored; an exception is captured in the report.
            tail: Seconds to keep polling after ``publish_fn`` returns (recovery capture).
            warmup: Seconds to poll before starting the publish (baseline).

        Returns:
            A :class:`PublishReport`.
        """
        self._stop.clear()
        self._reports = {name: SurfaceReport(name=name) for name in self.surfaces}

        threads = [
            threading.Thread(target=self._poll_loop, args=(name, fn), daemon=True) for name, fn in self.surfaces.items()
        ]
        for t in threads:
            t.start()

        # Baseline before the publish so a surface that is *already* failing is visible.
        self._stop.wait(warmup)

        publish_error: str | None = None
        pub_start = time.monotonic()
        try:
            publish_fn()
        except Exception as exc:  # noqa: BLE001 — capture, don't crash the probe
            publish_error = f"{type(exc).__name__}: {exc}"
        publish_seconds = time.monotonic() - pub_start

        # Keep polling so we record the recovery edge.
        self._stop.wait(tail)
        self._stop.set()
        for t in threads:
            t.join(timeout=self.interval * 4)

        return PublishReport(
            surfaces=self._reports,
            publish_seconds=publish_seconds,
            publish_error=publish_error,
        )
