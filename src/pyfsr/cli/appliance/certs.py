"""``pyfsr appliance certs`` — appliance TLS certificate verbs (``csadm certs``).

The self-signed certificate FortiSOAR ships with is valid for one year; once it
expires the DAS/API layer starts failing with ``Unable to load API credentials
from cache or DAS``. The documented fix is to regenerate it with
``csadm certs --generate <hostname>`` and restart services — that's what
:func:`regenerate` wraps. All ``csadm certs`` subcommands need root (sudo).
"""

from __future__ import annotations

from .transport import Transport


def regenerate(transport: Transport, hostname: str, *, yes: bool = False, timeout: float = 120.0) -> str:
    """Regenerate the appliance's self-signed cert (``csadm certs --generate <hostname>``).

    This replaces the on-disk certificate; services must be restarted afterwards
    (``service restart`` / ``csadm services --restart``) for the new cert to take
    effect. Because it mutates appliance state, it refuses without ``yes=True``.

    Args:
        hostname: the FQDN to issue the cert for (typically the appliance's
            ``hostname`` — the cert CN clients will validate against).
        yes: confirmation gate; without it the call raises rather than run.

    Returns:
        The ``csadm`` output (stripped).
    """
    if not isinstance(hostname, str) or not hostname.strip():
        raise ValueError("regenerate() requires a non-empty hostname")
    if not yes:
        raise PermissionError(
            f"refusing to regenerate the appliance certificate for {hostname!r} without "
            "confirmation (pass --yes); services must be restarted afterwards"
        )
    return (
        transport.run(["csadm", "certs", "--generate", hostname.strip()], sudo=True, timeout=timeout)
        .check()
        .stdout.strip()
    )
