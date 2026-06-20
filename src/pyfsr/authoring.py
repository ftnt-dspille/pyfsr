"""Compile playbook YAML to the FortiSOAR import envelope.

This is the bridge between the **fsr_playbooks** compiler (YAML → IR → FSR JSON)
and pyfsr's write path. It deliberately does **no** network I/O — it turns YAML
text into the ``{"type": "workflow_collections", "data": [...]}`` envelope that
:meth:`pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_export`
already knows how to push. The deploy step lives next to the client; this module
only compiles.

The compiler is an **optional** dependency: core pyfsr never imports it. Install
it with ``pip install "pyfsr[playbooks]"``. Until then, :func:`compile_playbook_yaml`
raises :class:`PlaybooksExtraNotInstalled` with that hint.

Example::

    from pyfsr.authoring import compile_playbook_yaml

    result = compile_playbook_yaml(open("alert.yaml").read())
    if result.ok:
        client.workflow_collections.import_export(result.fsr_json)
    else:
        for diag in result.errors:
            print(diag)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class PlaybooksExtraNotInstalled(ImportError):
    """Raised when the optional ``fsr_playbooks`` compiler is not installed."""

    def __init__(self, original: Exception | None = None) -> None:
        super().__init__('the playbook compiler is not installed — run: pip install "pyfsr[playbooks]"')
        self.original = original


def _load_compiler():
    """Import the fsr_playbooks compiler, translating a missing dep to a clear error."""
    try:
        from fsr_playbooks import compile_yaml
        from fsr_playbooks._db import default_db_path
    except ImportError as exc:  # pragma: no cover - exercised via the missing-extra test
        raise PlaybooksExtraNotInstalled(exc) from exc
    return compile_yaml, default_db_path


@dataclass
class CompiledPlaybook:
    """Result of compiling playbook YAML.

    ``fsr_json`` is the FortiSOAR export envelope ready for
    :meth:`~pyfsr.api.workflow_collections.WorkflowCollectionsAPI.import_export`
    (``None`` when compilation produced blocking errors). ``errors`` holds every
    diagnostic (both ``error`` and ``warning`` severities) as dicts; ``warnings``
    is the warning-only subset. ``ok`` is True only when there are no blocking
    errors and an envelope was produced.
    """

    fsr_json: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = False

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return [e for e in self.errors if e.get("severity") == "warning"]

    @property
    def blocking(self) -> list[dict[str, Any]]:
        return [e for e in self.errors if e.get("severity") != "warning"]

    @property
    def collection_names(self) -> list[str]:
        return [c.get("name", "") for c in (self.fsr_json or {}).get("data", [])]

    @property
    def playbook_names(self) -> list[str]:
        names: list[str] = []
        for col in (self.fsr_json or {}).get("data", []):
            for wf in col.get("workflows", []) or []:
                names.append(wf.get("name", ""))
        return names


def compile_playbook_yaml(
    text: str,
    *,
    db_path: str | Path | None = None,
    lax_codes: set[str] | None = None,
) -> CompiledPlaybook:
    """Compile playbook YAML text into a :class:`CompiledPlaybook`.

    Args:
        text: the playbook YAML source.
        db_path: path to the fsr_playbooks reference catalog. Defaults to the
            packaged catalog (``fsr_playbooks._db.default_db_path()``).
        lax_codes: optional set of diagnostic codes to downgrade from error to
            warning (forwarded to the compiler).

    Raises:
        PlaybooksExtraNotInstalled: if the ``pyfsr[playbooks]`` extra is missing.

    Returns:
        A :class:`CompiledPlaybook` with the export envelope and diagnostics.
    """
    compile_yaml, default_db_path = _load_compiler()
    resolved = Path(db_path) if db_path is not None else default_db_path()
    result = compile_yaml(text, resolved, lax_codes=lax_codes)
    errors = [e.to_dict() for e in result.errors]
    return CompiledPlaybook(fsr_json=result.fsr_json, errors=errors, ok=result.ok)


def format_diagnostic(diag: dict[str, Any]) -> str:
    """Render one diagnostic dict as a single human-readable line."""
    sev = diag.get("severity", "error").upper()
    code = diag.get("code", "")
    path = diag.get("path", "")
    msg = diag.get("message", "")
    loc = f" at {path}" if path else ""
    line = f"[{sev}] {code}{loc}: {msg}"
    if diag.get("suggestion"):
        line += f" (suggestion: {diag['suggestion']})"
    if diag.get("near"):
        line += f" (near: {diag['near']})"
    return line
