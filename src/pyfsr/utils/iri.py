"""Shared IRI-parsing helpers used across ``api/*.py`` modules.

``module_from_iri`` was independently reimplemented (identically) in
``_record_module.py`` and ``comments.py``. The trailing-segment extraction
(``uuid_from_iri``) was hand-rolled at several more call sites
(``manual_input.py``, ``ai.py``, ``playbooks.py``) as
``x.rstrip("/").rsplit("/", 1)[-1]`` with varying None/empty guards. These are
the shared copies.
"""

from __future__ import annotations


def module_from_iri(iri: str) -> str:
    """Return the module segment of a record IRI.

    ``/api/3/alerts/<uuid>`` -> ``alerts`` (the second-to-last path segment).
    Raises ``ValueError`` if ``iri`` doesn't have enough segments.
    """
    parts = [p for p in iri.split("/") if p]
    if len(parts) >= 2:
        return parts[-2]
    raise ValueError(f"Cannot derive module from record IRI: {iri!r}")


def uuid_from_iri(iri: str | None) -> str | None:
    """Return the trailing path segment of an IRI (its uuid/pk), or ``None``.

    Tolerates ``None``/empty input and a trailing slash (``.../<uuid>/`` and
    ``.../<uuid>`` both yield ``<uuid>``).
    """
    if not iri:
        return None
    tail = iri.rstrip("/").rsplit("/", 1)[-1]
    return tail or None
