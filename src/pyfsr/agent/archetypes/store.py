"""The companion archetype store -- a writable SQLite DB of archetype records.

Mirrors :func:`pyfsr.authoring.warm_catalog`'s SQLite style (plain ``sqlite3``, no ORM).
The default DB lives at ``~/.cache/pyfsr/archetypes.db`` -- the same per-user cache
convention as the warmed reference catalog (``~/.cache/pyfsr/fsr_reference.db``), so all of
pyfsr's runtime state is in one place. Pass an explicit ``db_path`` (e.g. a ``tmp_path``) for
tests or to keep a separate archetype library.

Seed archetypes ship as ``*.json`` under the package ``seed/`` dir and are loaded into a
freshly-created (empty) store on first use via :meth:`~pyfsr.agent.archetypes.store.ArchetypeStore.seed_if_empty`.
The seed dir ships the curated ``reconcile-and-report`` archetype (step 3); more archetypes can be
added by dropping further ``*.json`` files alongside it.

Example::

    from pyfsr.agent.archetypes import ArchetypeStore, Archetype

    store = ArchetypeStore()
    store.put(my_archetype)
    got = store.get(my_archetype.name)
    print(store.list())
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .record import Archetype

_SCHEMA_VERSION = "1"


def _default_db_path() -> Path:
    """A writable per-user cache location for the archetype store.

    Honors ``XDG_CACHE_HOME``; defaults to ``~/.cache/pyfsr/archetypes.db`` (same convention
    as the warmed reference catalog's ``_default_cache_db`` in :mod:`pyfsr.authoring`).
    """
    base = os.environ.get("XDG_CACHE_HOME")
    cache = Path(base) if base else (Path.home() / ".cache")
    return cache / "pyfsr" / "archetypes.db"


def _seed_dir() -> Path:
    """The shipped seed-archetype directory (``<package>/archetypes/seed/``)."""
    return Path(__file__).parent / "seed"


class ArchetypeStore:
    """CRUD over the companion archetype DB.

    Each method opens a short-lived connection (no lingering locks), so a store instance is
    cheap to hold and safe across sequential calls. Record bodies are stored as the JSON
    produced by :meth:`~pyfsr.agent.archetypes.record.Archetype.to_json`.
    """

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else _default_db_path()
        self._init_db()

    # ------------------------------------------------------------------ schema
    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._txn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS archetypes ("
                "  name TEXT PRIMARY KEY,"
                "  record TEXT NOT NULL,"
                "  created_at TEXT NOT NULL,"
                "  updated_at TEXT NOT NULL)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", _SCHEMA_VERSION),
            )

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------- reads
    def list(self) -> list[str]:
        """Return the names of all stored archetypes, sorted."""
        with self._txn() as conn:
            rows = conn.execute("SELECT name FROM archetypes ORDER BY name").fetchall()
        return [r[0] for r in rows]

    def get(self, name: str) -> Archetype | None:
        """Return the archetype named ``name``, or ``None`` if absent."""
        with self._txn() as conn:
            row = conn.execute("SELECT record FROM archetypes WHERE name = ?", (name,)).fetchone()
        return Archetype.from_json(row[0]) if row else None

    # ------------------------------------------------------------------ writes
    def put(self, archetype: Archetype) -> Archetype:
        """Upsert ``archetype`` (keyed by ``name``) and return it.

        Stamps ``created_at`` on insert and refreshes ``updated_at`` on every write;
        ``created_at`` is preserved across updates.
        """
        now = _now_iso()
        with self._txn() as conn:
            conn.execute(
                "INSERT INTO archetypes (name, record, created_at, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET record = excluded.record, "
                "updated_at = excluded.updated_at",
                (archetype.name, archetype.to_json(), now, now),
            )
        return archetype

    def delete(self, name: str) -> bool:
        """Delete the archetype named ``name``; return ``True`` if a row was removed."""
        with self._txn() as conn:
            cur = conn.execute("DELETE FROM archetypes WHERE name = ?", (name,))
            return cur.rowcount > 0

    # -------------------------------------------------------------------- seed
    def seed_if_empty(self, seed_dir: str | os.PathLike[str] | None = None) -> int:
        """Load every ``*.json`` archetype from ``seed_dir`` into an empty store.

        No-op (returns 0) if the store already holds archetypes. ``seed_dir`` defaults to the
        shipped package seed dir, which carries the curated ``reconcile-and-report`` archetype.
        Returns the number of archetypes loaded.

        A malformed seed file is skipped with a warning rather than aborting the batch, so one
        bad seed never blocks the rest.
        """
        if self.list():
            return 0
        src = Path(seed_dir) if seed_dir is not None else _seed_dir()
        loaded = 0
        for path in sorted(src.glob("*.json")):
            try:
                archetype = Archetype.from_json(path.read_text(encoding="utf-8"))
            except Exception as exc:  # pragma: no cover - defensive; one bad seed skips
                import warnings

                warnings.warn(f"skipping malformed seed {path.name}: {exc}", stacklevel=2)
                continue
            self.put(archetype)
            loaded += 1
        return loaded


def _now_iso() -> str:
    """UTC now as an ISO-8601 string (the ``Z`` suffix keeps it sortable + unambiguous)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
