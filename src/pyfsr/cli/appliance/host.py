"""``pyfsr appliance host`` — OS-level resource metrics (mem / swap / load / RSS / disk).

Typed wrappers over ``free`` / ``ps`` / ``/proc/loadavg`` / ``df`` so callers get
structured values instead of awk-ing command output. None of these need sudo.

The headline call is :func:`snapshot`, which gathers mem, swap, load, per-pattern
process RSS, and (optionally) disk in **one** SSH round-trip and returns a typed
:class:`HostSnapshot` — the parsing every troubleshooting script otherwise
re-implements lives here, tested once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .transport import Transport, TransportError

# Default process patterns worth tracking on a FortiSOAR box: the celery worker
# pool (sticky-RSS suspect) and the integrations uwsgi workers (unbounded-alloc
# suspect). Each is a regex matched against the full `ps` command line.
DEFAULT_PROC_PATTERNS: dict[str, str] = {
    "celeryd": r"celery\b.*worker",
    "integrations": r"integrations_wsgi",
}


@dataclass
class MemInfo:
    """Memory + swap, in MB (parsed from ``free -m``)."""

    total_mb: int
    used_mb: int
    free_mb: int
    swap_total_mb: int
    swap_used_mb: int


@dataclass
class LoadAvg:
    """1/5/15-minute load averages (from ``/proc/loadavg``)."""

    load1: float
    load5: float
    load15: float


@dataclass
class ProcRss:
    """Aggregate resident memory for processes whose command line matches ``pattern``."""

    pattern: str
    count: int
    sum_mb: float
    peak_mb: float


@dataclass
class DiskUsage:
    """Filesystem usage for a path, in MB (from ``df -Pm``)."""

    path: str
    size_mb: int
    used_mb: int
    avail_mb: int
    use_pct: int


@dataclass
class HostSnapshot:
    """One consistent sample of host resources (see :func:`snapshot`)."""

    mem: MemInfo
    load: LoadAvg
    procs: dict[str, ProcRss] = field(default_factory=dict)
    disk: DiskUsage | None = None

    def summary(self) -> str:
        """A compact one-line human summary (the ``fmt()`` every script hand-rolls)."""
        parts = [
            f"mem {self.mem.used_mb}/{self.mem.total_mb}MB",
            f"swap {self.mem.swap_used_mb}/{self.mem.swap_total_mb}MB",
        ]
        for name, p in self.procs.items():
            parts.append(f"{name} {p.sum_mb}MB/{p.count}w (peak {p.peak_mb}MB)")
        parts.append(f"load {self.load.load1}")
        if self.disk:
            parts.append(f"{self.disk.path} {self.disk.use_pct}%")
        return " | ".join(parts)


def meminfo(transport: Transport) -> MemInfo:
    """Memory + swap usage in MB.

    Raises :class:`~pyfsr.cli.appliance.transport.TransportError` if the command
    produced no usable output (a real host always reports a non-zero total).
    """
    out = transport.run(["free", "-m"]).stdout
    return _require_captured_mem(_parse_meminfo(out), source="free -m")


def loadavg(transport: Transport) -> LoadAvg:
    """1/5/15-minute load averages."""
    out = transport.run(["cat", "/proc/loadavg"]).stdout
    return _parse_loadavg(out)


def process_rss(transport: Transport, pattern: str) -> ProcRss:
    """Summed/peak RSS (MB) and count for processes whose command line matches ``pattern``.

    ``pattern`` is a Python regex matched against each process's full argv (e.g.
    ``r"celery\\b.*worker"``). Far less brittle than the ``ps | awk '/[c]elery/'``
    one-liners scripts copy around.
    """
    # rss in KB + full command line; '=' suppresses the header so every line is data.
    out = transport.run(["ps", "-e", "-o", "rss=,args="]).stdout
    return _parse_process_rss(out, pattern)


def disk(transport: Transport, path: str = "/opt/cyops") -> DiskUsage:
    """Filesystem usage (MB) for ``path``."""
    # -P = POSIX one-line-per-fs (no wrapping); -m = MB units.
    out = transport.run(["df", "-Pm", path]).stdout
    return _parse_disk(out, path)


def snapshot(
    transport: Transport,
    *,
    procs: dict[str, str] | None = None,
    disk_path: str | None = None,
) -> HostSnapshot:
    """One consistent sample of mem, swap, load, per-pattern process RSS, and disk.

    Gathers everything in a **single** SSH round-trip (so the numbers are coherent)
    and returns a typed :class:`HostSnapshot`. ``procs`` maps a label → regex
    (defaults to :data:`DEFAULT_PROC_PATTERNS`: ``celeryd`` and ``integrations``);
    pass ``disk_path`` to include a filesystem.
    """
    procs = DEFAULT_PROC_PATTERNS if procs is None else procs
    # Emit clearly delimited sections in one command, then parse each below. Keeping
    # the (small) shell here — rather than 4 separate round-trips — is what makes the
    # sample coherent; the parsing is the part that matters and it's all typed.
    script = "echo '@@FREE'; free -m; echo '@@LOAD'; cat /proc/loadavg; echo '@@PS'; ps -e -o rss=,args="
    if disk_path:
        script += f"; echo '@@DF'; df -Pm {disk_path}"
    out = transport.run(["sh", "-c", script]).stdout
    sections = _split_sections(out)

    # A capture can come back empty or truncated — most commonly when the box is
    # under heavy load/swap and the SSH command returns no (or partial) output
    # *without* raising. Parsed naively that yields an all-zeros snapshot that is
    # indistinguishable from a real reading and silently corrupts callers (e.g. a
    # spurious "0 worker" pool-collapse). A live host always reports a non-zero
    # memory total, so validate the headline section before trusting the rest.
    mem = _require_captured_mem(_parse_meminfo(sections.get("FREE", "")), source="snapshot")

    return HostSnapshot(
        mem=mem,
        load=_parse_loadavg(sections.get("LOAD", "")),
        procs={label: _parse_process_rss(sections.get("PS", ""), pat) for label, pat in procs.items()},
        disk=_parse_disk(sections.get("DF", ""), disk_path) if disk_path else None,
    )


# --- capture validation --------------------------------------------------------


def _require_captured_mem(mem: MemInfo, *, source: str) -> MemInfo:
    """Return ``mem`` unchanged, or raise if it is a degenerate (all-zeros) read.

    ``free -m`` on any running host reports a non-zero ``total``; a zero total
    means the command produced empty or truncated output (the parsers default to
    zero on missing input). Surfacing this as a
    :class:`~pyfsr.cli.appliance.transport.TransportError` lets
    callers' existing transport-error handling skip the bad sample instead of
    treating ``0 MB`` as real data.
    """
    if mem.total_mb <= 0:
        raise TransportError(
            f"{source}: captured no host metrics (memory total parsed as "
            f"{mem.total_mb} MB — empty or truncated command output)"
        )
    return mem


# --- parsers (pure functions — unit-tested without a live box) -----------------


def _split_sections(out: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in out.splitlines():
        if line.startswith("@@"):
            if current is not None:
                sections[current] = "\n".join(buf)
            current, buf = line[2:].strip(), []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf)
    return sections


def _parse_meminfo(out: str) -> MemInfo:
    total = used = free = swt = swu = 0
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == "Mem:" and len(parts) >= 4:
            total, used, free = int(parts[1]), int(parts[2]), int(parts[3])
        elif parts and parts[0] == "Swap:" and len(parts) >= 3:
            swt, swu = int(parts[1]), int(parts[2])
    return MemInfo(total, used, free, swt, swu)


def _parse_loadavg(out: str) -> LoadAvg:
    parts = out.split()
    vals = [float(p) for p in parts[:3]] if len(parts) >= 3 else [0.0, 0.0, 0.0]
    return LoadAvg(*vals)


def _parse_process_rss(ps_out: str, pattern: str) -> ProcRss:
    rx = re.compile(pattern)
    total = peak = count = 0
    for line in ps_out.splitlines():
        line = line.strip()
        if not line:
            continue
        rss_str, _, cmd = line.partition(" ")
        if not cmd or not rx.search(cmd):
            continue
        try:
            rss_kb = int(rss_str)
        except ValueError:
            continue
        total += rss_kb
        peak = max(peak, rss_kb)
        count += 1
    return ProcRss(pattern, count, round(total / 1024, 1), round(peak / 1024, 1))


def _parse_disk(out: str, path: str | None) -> DiskUsage:
    for line in out.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) >= 6:
            return DiskUsage(
                path=path or parts[5],
                size_mb=int(parts[1]),
                used_mb=int(parts[2]),
                avail_mb=int(parts[3]),
                use_pct=int(parts[4].rstrip("%")),
            )
    return DiskUsage(path or "?", 0, 0, 0, 0)
