"""``pyfsr appliance info`` — one-shot identity card."""

from __future__ import annotations

from .facts import Facts


def identity(facts: Facts) -> dict[str, str]:
    """Resolve the 'where am I / what are the magic values' card.

    Device UUID is shown masked (it doubles as the DB/ES password).
    """
    uuid = facts.device_uuid()
    card = {
        "target": facts.transport.target,
        "fsr_version": facts.fsr_version() or "(unknown)",
        "device_uuid": _mask(uuid),
        "content_db": facts.content_db(),
        "db_user": "cyberpgsql",
    }
    return card


def _mask(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]}"
