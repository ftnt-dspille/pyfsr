"""``pyfsr appliance license`` — licensing / identity verbs.

The device UUID is the value that eats sessions: it doubles as the
``cyberpgsql`` / ``elastic`` password, and on a FortiCloud-drifted box the
*entitlement* UUID that ``csadm`` reports diverges from the install-time UUID
that the DB/ES passwords were provisioned with — so ``csadm``'s value silently
fails Postgres auth (see :mod:`.facts`). ``license drift`` makes that mismatch a
first-class, gate-able check instead of a half-session mystery.
"""

from __future__ import annotations

from dataclasses import dataclass

from .facts import _extract_uuid
from .transport import Transport, TransportError

# The install-time UUID file: csadmin-readable (no sudo), and the value the
# Postgres/ES passwords were actually provisioned with.
_DEVICE_UUID_FILE = "/home/csadmin/device_uuid"


def show(transport: Transport) -> str:
    """Raw ``csadm license --show-details`` output (licensing identity card)."""
    return transport.run(["csadm", "license", "--show-details"], sudo=True).check().stdout.strip()


def device_uuid_from_file(transport: Transport) -> str | None:
    """The install-time device UUID from ``/home/csadmin/device_uuid`` (no sudo).

    This is the authoritative DB/ES password value. Returns ``None`` if the file
    is absent or unreadable.
    """
    res = transport.run(["cat", _DEVICE_UUID_FILE])
    return _extract_uuid(res.stdout) if res.ok else None


def device_uuid_from_csadm(transport: Transport) -> str | None:
    """The *current entitlement* device UUID from ``csadm`` (needs root).

    On a FortiCloud-drifted box this differs from the file value and will fail
    ``cyberpgsql`` auth. Returns ``None`` if csadm fails.
    """
    res = transport.run(["csadm", "license", "--get-device-uuid"], sudo=True)
    return _extract_uuid(res.stdout) if res.ok else None


def device_uuid(transport: Transport) -> str:
    """The device UUID to use as the DB/ES password: file first, csadm fallback.

    Mirrors :meth:`.facts.Facts.device_uuid` so the verb and the DB layer agree
    on which value is authoritative.
    """
    uuid = device_uuid_from_file(transport) or device_uuid_from_csadm(transport)
    if not uuid:
        raise TransportError(f"could not resolve device UUID ({_DEVICE_UUID_FILE} + csadm both failed)")
    return uuid


@dataclass
class DriftReport:
    """Result of comparing the install-time UUID against csadm's entitlement UUID."""

    file_uuid: str | None
    csadm_uuid: str | None
    drifted: bool
    verdict: str


def drift(transport: Transport) -> DriftReport:
    """Compare the install-time file UUID against csadm's entitlement UUID.

    A drift means ``csadm``'s value would fail Postgres/ES auth; the file value
    (used by the DB layer) is the one that still works. Either source missing is
    reported rather than silently treated as a match.
    """
    file_uuid = device_uuid_from_file(transport)
    csadm_uuid = device_uuid_from_csadm(transport)

    if file_uuid is None and csadm_uuid is None:
        verdict = "UNKNOWN (neither file nor csadm returned a UUID)"
        drifted = False
    elif file_uuid is None:
        verdict = f"UNKNOWN (no {_DEVICE_UUID_FILE}; csadm only)"
        drifted = False
    elif csadm_uuid is None:
        verdict = "UNKNOWN (csadm did not return a UUID)"
        drifted = False
    elif file_uuid == csadm_uuid:
        verdict = "ok (file == csadm; no entitlement drift)"
        drifted = False
    else:
        verdict = "DRIFT (csadm entitlement UUID != install-time UUID — csadm value fails DB/ES auth)"
        drifted = True

    return DriftReport(file_uuid=file_uuid, csadm_uuid=csadm_uuid, drifted=drifted, verdict=verdict)
