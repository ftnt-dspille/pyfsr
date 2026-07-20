"""Unit tests for ``pyfsr.cli.appliance.host`` — metric parsers and the
:func:`~pyfsr.cli.appliance.host.snapshot` capture-validation guard.

The parsers are pure functions, so most of this runs without a live box. The
``snapshot``/``meminfo`` tests drive a tiny :class:`ScriptedTransport` that
returns canned (or empty) stdout, reproducing the all-zeros-under-load bug:
when the SSH command yields empty/truncated output the parsers default every
field to zero, and the old code returned that as a real ``HostSnapshot``. The
guard now raises :class:`TransportError` instead.
"""

from __future__ import annotations

import pytest

from pyfsr.cli.appliance import host as host_cmds
from pyfsr.cli.appliance.host import (
    HostSnapshot,
    LoadAvg,
    MemInfo,
    ProcRss,
    _parse_loadavg,
    _parse_meminfo,
    _parse_process_rss,
    _require_captured_mem,
    _split_sections,
    meminfo,
    snapshot,
)
from pyfsr.cli.appliance.transport import CommandResult, Transport, TransportError

# A realistic `free -m` block and the assembled snapshot sections.
FREE_RAW = (
    "              total        used        free      shared  buff/cache   available\n"
    "Mem:          23768       12363        1024         200       10381       10000\n"
    "Swap:          8191        2684        5507\n"
)
LOADAVG_RAW = "1.05 1.10 1.20 2/1234 567890\n"
PS_RAW = (
    "  204800 /opt/.../uwsgi --ini integrations_wsgi.ini\n"
    "  102400 /opt/.../uwsgi --ini integrations_wsgi.ini\n"
    "   51200 /usr/bin/celery -A x worker\n"
)


class ScriptedTransport(Transport):
    """Return a fixed stdout for every command (the failure knob these tests need)."""

    target = "scripted"

    def __init__(self, stdout: str) -> None:
        self._stdout = stdout

    def run(self, argv, **_kw) -> CommandResult:  # type: ignore[override]
        return CommandResult(argv=argv, returncode=0, stdout=self._stdout, stderr="")


def _snapshot_stdout(*, free: str = FREE_RAW, load: str = LOADAVG_RAW, ps: str = PS_RAW) -> str:
    """Assemble the delimited stdout that :func:`snapshot` parses."""
    return "\n".join(["@@FREE", free, "@@LOAD", load.strip(), "@@PS", ps.strip()])


# --------------------------------------------------------------- pure parsers


def test_parse_meminfo_reads_mem_and_swap() -> None:
    mem = _parse_meminfo(FREE_RAW)
    assert mem == MemInfo(total_mb=23768, used_mb=12363, free_mb=1024, swap_total_mb=8191, swap_used_mb=2684)


def test_parse_meminfo_empty_is_all_zeros() -> None:
    # The parser itself stays a pure function: empty in → zeros out (no raising).
    assert _parse_meminfo("") == MemInfo(total_mb=0, used_mb=0, free_mb=0, swap_total_mb=0, swap_used_mb=0)


def test_parse_loadavg_and_empty() -> None:
    assert _parse_loadavg(LOADAVG_RAW) == LoadAvg(load1=1.05, load5=1.10, load15=1.20)
    assert _parse_loadavg("") == LoadAvg(load1=0.0, load5=0.0, load15=0.0)


def test_parse_process_rss_sums_matches() -> None:
    p = _parse_process_rss(PS_RAW, r"integrations_wsgi")
    assert isinstance(p, ProcRss)
    assert p.count == 2
    assert p.sum_mb == pytest.approx((204800 + 102400) / 1024, rel=1e-3)
    assert p.peak_mb == pytest.approx(204800 / 1024, rel=1e-3)


def test_split_sections_partial_bodies_are_empty() -> None:
    # Markers present but no bodies → each section empty (the truncated-output shape).
    assert _split_sections("@@FREE\n@@LOAD\n@@PS\n") == {"FREE": "", "LOAD": "", "PS": ""}


# --------------------------------------------------------------- capture guard


def test_require_captured_mem_passes_real_reading() -> None:
    mem = MemInfo(total_mb=23768, used_mb=12363, free_mb=1024, swap_total_mb=8191, swap_used_mb=2684)
    assert _require_captured_mem(mem, source="x") is mem


@pytest.mark.parametrize("total", [0, -1])
def test_require_captured_mem_rejects_degenerate(total: int) -> None:
    with pytest.raises(TransportError, match="captured no host metrics"):
        _require_captured_mem(
            MemInfo(total_mb=total, used_mb=0, free_mb=0, swap_total_mb=0, swap_used_mb=0), source="snapshot"
        )


# --------------------------------------------------------------- snapshot()


def test_snapshot_parses_full_capture() -> None:
    snap = snapshot(ScriptedTransport(_snapshot_stdout()))
    assert isinstance(snap, HostSnapshot)
    assert snap.mem.total_mb == 23768
    assert snap.mem.swap_used_mb == 2684
    assert snap.load == LoadAvg(load1=1.05, load5=1.10, load15=1.20)
    assert snap.procs["integrations"].count == 2


def test_snapshot_raises_on_empty_output() -> None:
    # The exact bug: command returned nothing (heavy load) → must raise, not 0 MB.
    with pytest.raises(TransportError, match="captured no host metrics"):
        snapshot(ScriptedTransport(""))


def test_snapshot_raises_on_truncated_output() -> None:
    # Markers present, FREE body missing → still a failed capture.
    truncated = _snapshot_stdout(free="")
    with pytest.raises(TransportError, match="captured no host metrics"):
        snapshot(ScriptedTransport(truncated))


def test_meminfo_raises_on_empty_output() -> None:
    with pytest.raises(TransportError, match="captured no host metrics"):
        meminfo(ScriptedTransport(""))


def test_meminfo_parses_real_output() -> None:
    assert meminfo(ScriptedTransport(FREE_RAW)).total_mb == 23768


def test_default_proc_patterns_present() -> None:
    # Guards against accidental rename of the two tracked pools.
    assert set(host_cmds.DEFAULT_PROC_PATTERNS) == {"celeryd", "integrations"}
