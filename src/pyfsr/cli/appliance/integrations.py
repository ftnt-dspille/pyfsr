"""``pyfsr appliance integrations`` -- the connector runtime's pip index.

When a connector is installed the platform pip-installs its ``requirements.txt``
into the integrations environment, using that environment's own ``pip.conf``.
Out of the box the file names only Fortinet's content mirror as ``index-url``,
so a connector that depends on a package the mirror does not carry (anything
published only to PyPI) installs with its dependency missing. Some appliances
carry PyPI as ``extra-index-url``; others do not, and no REST API or UI setting
writes the file -- it has to be edited on the box.

The file ships **immutable** (``chattr +i``), so a plain write fails with
"Operation not permitted". :func:`ensure_extra_index` clears the flag, writes,
and puts the flag back in a ``finally`` so a failed write never leaves the file
unlocked. It is idempotent: a URL already listed is a no-op that touches
nothing.

The new content is passed as a command argument, never on stdin: the
transports put the sudo password on stdin, and with passwordless sudo nothing
consumes it, so it would land in the file.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .transport import Transport, TransportError

#: The integrations environment's pip config (live-verified on 8.0.0).
PIP_CONF = "/opt/cyops/configs/integrations/packages/integrations_env/pip.conf"

#: PyPI's simple index, the usual extra index for a PyPI-only dependency.
PYPI_SIMPLE = "https://pypi.org/simple/"


class PipIndex(BaseModel):
    """The index settings in the integrations ``pip.conf``."""

    path: str
    index_url: str | None = None
    extra_index_urls: list[str] = Field(default_factory=list)
    immutable: bool = False
    text: str = ""

    def __str__(self) -> str:
        extra = " ".join(self.extra_index_urls) or "(none)"
        lock = " [immutable]" if self.immutable else ""
        return f"{self.path}{lock}\n  index-url: {self.index_url or '(none)'}\n  extra-index-url: {extra}"


class IndexChange(BaseModel):
    """Outcome of :func:`ensure_extra_index`."""

    url: str
    changed: bool
    before: PipIndex
    after: PipIndex
    backup: str | None = None

    @property
    def ok(self) -> bool:
        """The URL is listed now, and the immutable flag is as it was before."""
        return self.url in self.after.extra_index_urls and self.after.immutable == self.before.immutable

    def __str__(self) -> str:
        if not self.changed:
            return f"{self.url} already an extra index -- no change"
        verdict = "ok" if self.ok else "FAILED"
        return f"added extra index {self.url}: {verdict} (backup {self.backup})\n{self.after}"


def _key(line: str) -> str:
    return line.split("=", 1)[0].strip().lower().replace("_", "-") if "=" in line else ""


def parse(text: str, *, path: str = PIP_CONF, immutable: bool = False) -> PipIndex:
    """Read ``index-url`` / ``extra-index-url`` from pip.conf text (``[global]`` or bare keys)."""
    index_url: str | None = None
    extras: list[str] = []
    section = "global"
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "global":
            continue
        key = _key(line)
        value = line.split("=", 1)[1].strip() if key else ""
        if key == "index-url":
            index_url = value or None
        elif key == "extra-index-url":
            extras.extend(value.split())
    return PipIndex(path=path, index_url=index_url, extra_index_urls=extras, immutable=immutable, text=text)


def add_extra_index(text: str, url: str) -> str:
    """Return pip.conf ``text`` with ``url`` added as an extra index.

    pip reads a whitespace-separated list, so an existing ``extra-index-url``
    line is extended in place; otherwise a new line goes at the end of
    ``[global]`` (or the end of the file when there are no sections).
    """
    lines = text.splitlines()
    section = "global"
    last_global = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "global":
            continue
        if line and not line.startswith(("#", ";")):
            last_global = i
        if _key(line) == "extra-index-url":
            lines[i] = f"{raw.rstrip()} {url}"
            return "\n".join(lines) + "\n"
    new = f"extra-index-url = {url}"
    if last_global is None:
        lines.append(new)
    else:
        lines.insert(last_global + 1, new)
    # pip ignores keys that sit above any section header.
    if not any(ln.strip().lower() == "[global]" for ln in lines):
        lines.insert(0, "[global]")
    return "\n".join(lines) + "\n"


def pip_index(transport: Transport, *, path: str = PIP_CONF) -> PipIndex:
    """Read the integrations pip index settings and the file's immutable flag. Read-only."""
    text = transport.run(["cat", path]).check().stdout
    attrs = transport.run(["lsattr", "-d", path], sudo=True).check().stdout
    flags = attrs.split()[0] if attrs.strip() else ""
    return parse(text, path=path, immutable="i" in flags)


def _write(transport: Transport, path: str, text: str) -> None:
    # `>` truncates in place, so the inode keeps its owner, mode and ACL.
    script = 'printf "%s" "$1" > "$2"'
    transport.run(["sh", "-c", script, "sh", text, path], sudo=True).check()


def ensure_extra_index(
    transport: Transport,
    url: str = PYPI_SIMPLE,
    *,
    path: str = PIP_CONF,
    yes: bool = False,
) -> IndexChange:
    """Add ``url`` as an extra pip index for the connector runtime. Gated by ``yes``.

    A no-op (no ``yes`` needed, nothing run but reads) when ``url`` is already
    listed. Otherwise it backs the file up to ``<path>.bak``, clears the
    immutable flag if set, writes, restores the flag in a ``finally``, then
    re-reads the file so the result reports what is actually on disk.
    """
    before = pip_index(transport, path=path)
    if url in before.extra_index_urls:
        return IndexChange(url=url, changed=False, before=before, after=before)
    if not yes:
        raise PermissionError(f"refusing to add {url!r} to {path} without confirmation (pass --yes)")

    backup = f"{path}.bak"
    transport.run(["cp", "-p", path, backup], sudo=True).check()
    if before.immutable:
        transport.run(["chattr", "-i", path], sudo=True).check()
    try:
        _write(transport, path, add_extra_index(before.text, url))
    finally:
        if before.immutable:
            relock = transport.run(["chattr", "+i", path], sudo=True)
            if not relock.ok:
                raise TransportError(
                    f"{path} was written but could not be made immutable again: {relock.stderr.strip()}"
                )
    after = pip_index(transport, path=path)
    return IndexChange(url=url, changed=True, before=before, after=after, backup=backup)
