"""Shared upload helper for the Content-Hub solution-pack installer.

Connectors and widgets both install via the same multipart endpoint —
``POST /api/3/solutionpacks/install`` — differing only in the ``$type`` query
value (``connector`` vs ``widget``). Centralized here so a wire-shape fix (e.g.
the uploaded file part's ``Content-Type``) lands for every caller at once
instead of drifting between independent copies.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..client import FortiSOAR


def upload_solutionpack(
    client: FortiSOAR,
    path: str,
    *,
    type_: str,
    replace: bool,
    content_type: str = "application/gzip",
) -> dict[str, Any]:
    """POST a ``.tgz`` to the solution-pack installer; returns the raw JSON response.

    Args:
        client: the :class:`~pyfsr.client.FortiSOAR` client.
        path: filesystem path to the ``.tgz`` bundle.
        type_: the ``$type`` query value (e.g. ``"connector"``, ``"widget"``).
        replace: overwrite an already-staged copy of this exact name+version
            (``$replace=true``); omitted (not ``$replace=false``) otherwise,
            matching what the UI sends.
        content_type: the uploaded file part's ``Content-Type``. Defaults to
            ``application/gzip`` — live-verified as what the Content-Hub UI
            itself sends. A ``mimetypes.guess_type``-derived alternative
            (``application/x-tar`` for connectors, ``application/octet-stream``
            as its fallback) caused a live 500 installing a genuinely new
            widget name (see ``docs/plans/WIDGET_UPLOAD_PUBLISH_PLAN.md``) —
            don't reintroduce a guessed mimetype here without re-verifying live.

    Returns:
        The raw decoded JSON response (never ``None``; a non-dict response is
        wrapped as ``{"result": resp}`` so callers can always index it).

    Raises:
        FileNotFoundError: if ``path`` doesn't exist.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"solution-pack bundle not found: {file_path}")
    params: dict[str, Any] = {"$type": type_}
    if replace:
        params["$replace"] = "true"
    with open(file_path, "rb") as f:
        resp = client.post(
            "/api/3/solutionpacks/install",
            files={"file": (file_path.name, f, content_type)},
            params=params,
            headers={"Content-Type": None},
        )
    return resp if isinstance(resp, dict) else {"result": resp}
