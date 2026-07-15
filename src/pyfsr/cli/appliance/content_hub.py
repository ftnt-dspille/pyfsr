"""``pyfsr appliance content-hub`` — Content Hub sync verbs.

Wraps ``csadm package content-hub sync`` — the Symfony ``app:contenthub:sync``
command that pulls the catalog + artifacts from the configured ``REPOSERVER``
(``product_yum_server``; ``OFFLINEREPO=true`` points it at a self-hosted
mirror). A ``--force`` sync re-fetches every entry regardless of its
``publishedDate`` (the gate a *scheduled* sync uses to decide "is this
newer?"), so it is how an appliance picks up a freshly-published override
without waiting for the scheduler.

``csadm`` exits 0 even when the sync does nothing useful (the same unreliable
exit-code behavior the ``service`` verbs see), so success is folded together
with an output-text check rather than trusted to the return code alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .transport import Transport

#: ``csadm`` prints this on a completed sync (the Symfony command's final
#: status line). The sync also emits per-entry progress; this anchors "done".
#: Live-verified on 8.0.0 (fsr-ga) against the self-hosted mirror.
_SYNC_DONE = re.compile(r"sync.*(?:complete|success|finished)", re.IGNORECASE)

#: A sync that could not reach the repo host / fetch the manifest. The
#: ``setup-appliance.sh`` sync prints these on a mirror that is down or whose
#: cert isn't trusted.
_SYNC_FAIL = re.compile(r"(could not|unable to|failed|error|refused|timed? ?out|404|not found)", re.IGNORECASE)


@dataclass
class SyncResult:
    """Outcome of a Content Hub sync.

    ``ok`` folds together the exit code and an output-text signal (``csadm``'s
    exit code is unreliable). ``output`` keeps the raw csadm text for
    diagnostics (it carries the per-entry fetch log).
    """

    force: bool
    ok: bool
    output: str

    def __str__(self) -> str:
        verdict = "ok" if self.ok else "FAILED"
        mode = "forced" if self.force else "scheduled"
        detail = f" — {self.output}" if self.output else ""
        return f"content-hub sync ({mode}): {verdict}{detail}"


def sync(transport: Transport, *, force: bool = True, yes: bool = False) -> SyncResult:
    """Run ``csadm package content-hub sync`` on the appliance. Gated by ``yes``.

    A *scheduled* sync (the default ``csadm`` runs on its cadence) only applies
    an override whose ``publishedDate`` is newer than the synced one; a **forced**
    sync (``--force``, the default here) re-fetches everything regardless. Use
    ``force=True`` (default) to pick up a freshly-published connector from a
    mirror immediately; pass ``force=False`` to run the same cadence gate the
    scheduler does.

    Mutating-ish (it rewrites the appliance's cached catalog + installs newly
    advertised connectors), so it is gated behind ``yes`` — pass ``--yes`` on
    the CLI.

    ``csadm`` exits 0 even when the sync logs an error (unreachable mirror,
    untrusted cert), so the returned ``ok`` folds a failure-text check in: a
    non-zero exit OR a ``_SYNC_FAIL`` line ⇒ ``ok=False``.
    """
    if not yes:
        mode = "forced" if force else "scheduled"
        raise PermissionError(f"refusing a {mode} content-hub sync without confirmation (pass --yes)")
    argv = ["csadm", "package", "content-hub", "sync"]
    if force:
        argv.append("--force")
    # The sync fetches up to ~900 entries + per-item artifacts over the network;
    # give it the same generous ceiling the whole-stack service bounce gets.
    res = transport.run(argv, sudo=True, timeout=600)
    text = (res.stdout or res.stderr).strip()
    ok = res.ok and not _SYNC_FAIL.search(text)
    return SyncResult(force=force, ok=ok, output=text)
