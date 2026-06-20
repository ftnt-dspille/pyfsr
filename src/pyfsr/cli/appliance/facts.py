"""Appliance facts — resolve the magic values once and memoize them.

This is the anti-session-waste core (plan §Implementation): device UUID (= the
Postgres/ES password), the install-specific content DB name, and the major
version. Everything is resolved lazily through a :class:`Transport` and cached on
the :class:`Facts` instance for the life of one CLI invocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .transport import Transport, TransportError

# Fixed-name, role-keyed service DBs (plan §Database registry). The content DB is
# deliberately absent — its name is install-specific and must be discovered.
FIXED_ROLE_DBS: dict[str, str] = {
    "das": "das",
    "gateway": "gateway",
    "connectors": "connectors",
    "notifier": "notifier",
    "data_archival": "data_archival",
}

CONTENT_ROLE = "content"

# A table that exists only in the content DB — used to fingerprint it.
_CONTENT_FINGERPRINT = "model_metadatas"


@dataclass
class Facts:
    """Lazily-resolved, memoized appliance facts over a transport."""

    transport: Transport
    _device_uuid: str | None = field(default=None, repr=False)
    _content_db: str | None = field(default=None, repr=False)

    # --- device identity -------------------------------------------------
    def device_uuid(self) -> str:
        """The 32-char device UUID. This doubles as the ``cyberpgsql`` /
        ``elastic`` password, so it is read into memory and never logged."""
        if self._device_uuid:
            return self._device_uuid
        # Primary: csadm (needs root). Fallback: the cached file (csadmin-readable,
        # so tried without then with sudo).
        res = self.transport.run(["csadm", "license", "--get-device-uuid"], sudo=True)
        uuid = _extract_uuid(res.stdout) if res.ok else None
        if not uuid:
            res = self.transport.run(["cat", "/home/csadmin/device_uuid"])
            uuid = _extract_uuid(res.stdout) if res.ok else None
        if not uuid:
            raise TransportError("could not resolve device UUID (csadm + /home/csadmin/device_uuid both failed)")
        self._device_uuid = uuid
        return uuid

    @property
    def db_password(self) -> str:
        """Postgres ``cyberpgsql`` password (= device UUID)."""
        return self.device_uuid()

    # --- database resolution --------------------------------------------
    def resolve_db(self, role: str | None = None, db: str | None = None) -> str:
        """Resolve a target DB name from an explicit ``db`` or a ``role``.

        Explicit ``--db`` wins. A fixed role maps directly. The ``content`` role
        (default) is discovered by fingerprinting for ``model_metadatas``.
        """
        if db:
            return db
        role = role or CONTENT_ROLE
        if role in FIXED_ROLE_DBS:
            return FIXED_ROLE_DBS[role]
        if role == CONTENT_ROLE:
            return self.content_db()
        raise TransportError(f"unknown DB role {role!r}; known roles: content, {', '.join(FIXED_ROLE_DBS)}")

    def content_db(self) -> str:
        """Discover the install-specific content DB (the one holding
        ``model_metadatas``). Cached after first resolution."""
        if self._content_db:
            return self._content_db
        names = self._list_databases()
        for name in names:
            if self._db_has_table(name, _CONTENT_FINGERPRINT):
                self._content_db = name
                return name
        raise TransportError(f"could not find the content DB (no DB among {names} has {_CONTENT_FINGERPRINT})")

    def _psql_env(self) -> dict[str, str]:
        return {"PGPASSWORD": self.db_password}

    def psql(
        self,
        sql: str,
        *,
        db: str,
        tuples_only: bool = True,
        timeout: float = 60.0,
    ) -> list[list[str]]:
        """Run SQL via ``psql`` against ``db`` and return rows as lists of cells.

        Uses ``-F$'\\x1f'`` (unit separator) as the field delimiter so values
        containing commas/pipes parse cleanly. Password goes via ``PGPASSWORD``
        env, never argv.
        """
        args = [
            "psql",
            "-U",
            "cyberpgsql",
            "-h",
            "127.0.0.1",
            "-d",
            db,
            "-A",  # unaligned
            "-F",
            "\x1f",
            "--no-psqlrc",
        ]
        if tuples_only:
            args.append("-t")
        args += ["-c", sql]
        res = self.transport.run(args, env=self._psql_env(), timeout=timeout).check()
        rows: list[list[str]] = []
        for line in res.stdout.splitlines():
            if not line.strip():
                continue
            rows.append(line.split("\x1f"))
        return rows

    def _list_databases(self) -> list[str]:
        rows = self.psql(
            "SELECT datname FROM pg_database WHERE datistemplate=false ORDER BY datname",
            db="postgres",
        )
        return [r[0] for r in rows if r and r[0]]

    def _db_has_table(self, db: str, table: str) -> bool:
        rows = self.psql(
            f"SELECT 1 FROM information_schema.tables WHERE table_name='{table}' LIMIT 1",
            db=db,
        )
        return bool(rows)

    # --- version ---------------------------------------------------------
    def fsr_version(self) -> str | None:
        """Best-effort FortiSOAR version from the installed RPM."""
        res = self.transport.run(
            ["rpm", "-q", "--qf", "%{VERSION}", "cyops-ui"],
        )
        if res.ok and res.stdout.strip() and "not installed" not in res.stdout:
            return res.stdout.strip()
        return None


def _extract_uuid(text: str) -> str | None:
    """Pull a 32-char hex UUID (no dashes) out of command output."""
    import re

    for token in text.split():
        token = token.strip().strip(":").lower()
        if re.fullmatch(r"[0-9a-f]{32}", token):
            return token
    return None
