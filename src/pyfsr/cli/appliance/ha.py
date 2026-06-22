"""``pyfsr appliance ha`` — FortiSOAR HA clustering verbs.

Thin wrappers over ``csadm ha`` subcommands (all require root). The list/health
verbs return typed dataclasses; ``*_raw`` escape hatches give the unparsed text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._text import dash_columns, kv_pairs, slice_columns, strip_ansi, to_float, to_int
from .transport import Transport


def _ha(transport: Transport, *args: str, timeout: float = 30.0) -> str:
    """Run ``csadm ha <args>`` under sudo, returning stripped stdout."""
    return transport.run(["csadm", "ha", *args], sudo=True, timeout=timeout).check().stdout.strip()


@dataclass
class HaNode:
    """One member of the HA cluster (a row of ``csadm ha list-nodes``)."""

    node_id: str
    name: str
    status: str
    role: str
    comment: str
    mode: str
    fsr_version: str
    is_current: bool  # the node marked with '*' (the box you queried)


@dataclass
class ResourceUsage:
    """Memory or swap usage from ``csadm ha show-health``."""

    total: str
    used: str
    avail: str
    percent: float


@dataclass
class DiskMount:
    """One filesystem row from ``csadm ha show-health``."""

    mountpoint: str
    device: str
    total: str
    used: str
    avail: str
    percent: float


@dataclass
class HaHealth:
    """Parsed ``csadm ha show-health`` summary."""

    node_name: str | None
    node_id: str | None
    mode: str | None
    services_status: str | None
    queued_workflows: int | None
    uptime: str | None
    memory: ResourceUsage | None = None
    swap: ResourceUsage | None = None
    disks: list[DiskMount] = field(default_factory=list)


def nodes(transport: Transport) -> list[HaNode]:
    """Typed ``csadm ha list-nodes`` — the HA cluster members."""
    return _parse_nodes(_ha(transport, "list-nodes"))


def nodes_raw(transport: Transport) -> str:
    """Unparsed ``csadm ha list-nodes`` text (escape hatch for :func:`nodes`)."""
    return _ha(transport, "list-nodes")


def health(transport: Transport) -> HaHealth:
    """Typed ``csadm ha show-health`` — node mode, service status, mem/swap/disk."""
    return _parse_health(_ha(transport, "show-health"))


def health_raw(transport: Transport) -> str:
    """Unparsed ``csadm ha show-health`` text (escape hatch for :func:`health`)."""
    return _ha(transport, "show-health")


def replication(transport: Transport) -> str:
    """``csadm ha get-replication-stat`` — DB replication lag and status (raw text)."""
    return _ha(transport, "get-replication-stat")


# --- parsers (pure functions — unit-tested without a live box) -----------------


def _parse_nodes(text: str) -> list[HaNode]:
    lines = [ln for ln in strip_ansi(text).splitlines() if ln.strip()]
    # Find the dash-rule line; the line above is the header, lines below are data.
    sep_idx = next((i for i, ln in enumerate(lines) if set(ln.strip()) <= {"-", " "} and "-" in ln), None)
    if sep_idx is None or sep_idx == 0:
        return []
    spans = dash_columns(lines[sep_idx])
    out: list[HaNode] = []
    for line in lines[sep_idx + 1 :]:
        cells = slice_columns(line, spans)
        if len(cells) < 7:
            continue
        node_id = cells[0]
        is_current = node_id.startswith("*")
        node_id = node_id.lstrip("* ").strip()
        out.append(
            HaNode(
                node_id=node_id,
                name=cells[1],
                status=cells[2],
                role=cells[3],
                comment=cells[4],
                mode=cells[5],
                fsr_version=cells[6],
                is_current=is_current,
            )
        )
    return out


def _parse_resource_row(data: str) -> ResourceUsage | None:
    # "total used avail percent" (avail labelled "free" for swap); whitespace-split.
    vals = data.split()
    if len(vals) < 4:
        return None
    return ResourceUsage(total=vals[0], used=vals[1], avail=vals[2], percent=to_float(vals[3], 0.0) or 0.0)


def _parse_health(text: str) -> HaHealth:
    text = strip_ansi(text)
    kv = kv_pairs(text)
    memory = swap = None
    disks: list[DiskMount] = []

    # Walk sections delimited by "<Name> Usage:" headers, each followed by a column
    # header, a dash rule, then data row(s).
    section: str | None = None
    seen_header = False
    for line in text.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("memory usage"):
            section, seen_header = "memory", False
        elif low.startswith("swap usage"):
            section, seen_header = "swap", False
        elif low.startswith("disk usage"):
            section, seen_header = "disk", False
        elif low.startswith("system load"):
            section = None
        elif section and s and not set(s) <= {"-", " "}:
            # First data-ish line after the section header is the column header.
            if not seen_header and ("total" in low or "mountpoint" in low):
                seen_header = True
                continue
            if section == "memory":
                memory = _parse_resource_row(s)
                section = None
            elif section == "swap":
                swap = _parse_resource_row(s)
                section = None
            elif section == "disk":
                cells = s.split()
                if len(cells) >= 6:
                    disks.append(
                        DiskMount(
                            mountpoint=cells[0],
                            device=cells[1],
                            total=cells[2],
                            used=cells[3],
                            avail=cells[4],
                            percent=to_float(cells[5], 0.0) or 0.0,
                        )
                    )

    return HaHealth(
        node_name=kv.get("Node Name"),
        node_id=kv.get("Node ID"),
        mode=kv.get("Mode"),
        services_status=kv.get("Services Status"),
        queued_workflows=to_int(kv.get("Queued Workflow Count")),
        uptime=kv.get("Uptime"),
        memory=memory,
        swap=swap,
        disks=disks,
    )
