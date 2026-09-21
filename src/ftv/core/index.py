"""The remembered scans, stored in SQLite.

This is the one module allowed to write, and the only thing it writes is the
index database. It is exempt from the write-surface audit for that reason.

SQLite rather than a JSON file: a scan of a large destination is hundreds of
thousands of rows, a JSON index would be fully parsed on every run and fully
rewritten on every re-scan, and a crash mid-rewrite would leave a corrupt index
backing a "safe to format" decision.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ftv.core.models import FileEntry, IndexedFile, Scan
from ftv.paths import default_db_path, validate_db_path

SCHEMA_VERSION = 1

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE scans (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            scanned_at      TEXT NOT NULL,
            use_mtime       INTEGER NOT NULL DEFAULT 1,
            skip_extensions TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE scan_roots (
            scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            path    TEXT NOT NULL
        );
        CREATE TABLE files (
            scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            path    TEXT NOT NULL,
            name    TEXT NOT NULL,
            size    INTEGER NOT NULL,
            mtime   REAL NOT NULL
        );
        CREATE INDEX files_lookup ON files (scan_id, name, size);
        CREATE INDEX scan_roots_scan ON scan_roots (scan_id);
        """,
    ),
)


class ScanExistsError(Exception):
    """Raised when saving over an existing scan without ``replace=True``."""


class ScanNotFoundError(Exception):
    """Raised when a named scan does not exist."""


class ScanLookup:
    """Answers name+size questions for one scan.

    Backed by the ``files_lookup`` index, so each question is one O(log n)
    probe rather than a scan of the table.
    """

    __slots__ = ("_conn", "_scan_id")

    def __init__(self, conn: sqlite3.Connection, scan_id: int) -> None:
        self._conn = conn
        self._scan_id = scan_id

    def find(self, name: str, size: int) -> list[IndexedFile]:
        rows = self._conn.execute(
            "SELECT path, name, size, mtime FROM files "
            "WHERE scan_id = ? AND name = ? AND size = ?",
            (self._scan_id, name, size),
        ).fetchall()
        return [
            IndexedFile(path=row["path"], name=row["name"], size=row["size"], mtime=row["mtime"])
            for row in rows
        ]


class Index:
    """Create, read and delete remembered scans."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    @classmethod
    def open(cls, db_path: Path | None = None, roots: Iterable[Path] = ()) -> Index:
        """Open (creating if needed) the index at ``db_path``.

        ``roots`` are validated against the database location so the index can
        never be written inside a destination or onto a card.
        """
        target = Path(db_path) if db_path is not None else default_db_path()
        validate_db_path(target, roots)
        target.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(target))

    def close(self) -> None:
        self._conn.close()

    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        current = self.schema_version()
        for version, script in _MIGRATIONS:
            if version > current:
                self._conn.executescript(script)
                self._conn.execute(f"PRAGMA user_version = {version}")
                self._conn.commit()

    def _scan_id(self, name: str) -> int:
        row = self._conn.execute("SELECT id FROM scans WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ScanNotFoundError(f"no scan named {name!r}")
        return int(row["id"])

    def save_scan(
        self,
        name: str,
        roots: Sequence[Path],
        entries: Iterable[FileEntry],
        *,
        use_mtime: bool,
        skip_extensions: frozenset[str],
        replace: bool = False,
    ) -> Scan:
        """Store a scan and its files in one transaction."""
        existing = self._conn.execute("SELECT id FROM scans WHERE name = ?", (name,)).fetchone()
        if existing is not None and not replace:
            raise ScanExistsError(
                f"a scan named {name!r} already exists; use refresh or pass replace=True"
            )

        scanned_at = datetime.now(UTC)
        root_tuple = tuple(Path(r) for r in roots)

        with self._conn:
            if existing is not None:
                self._conn.execute("DELETE FROM scans WHERE id = ?", (int(existing["id"]),))
            cursor = self._conn.execute(
                "INSERT INTO scans (name, scanned_at, use_mtime, skip_extensions) "
                "VALUES (?, ?, ?, ?)",
                (
                    name,
                    scanned_at.isoformat(),
                    1 if use_mtime else 0,
                    json.dumps(sorted(skip_extensions)),
                ),
            )
            scan_id = int(cursor.lastrowid)
            self._conn.executemany(
                "INSERT INTO scan_roots (scan_id, path) VALUES (?, ?)",
                [(scan_id, str(root)) for root in root_tuple],
            )
            count = 0
            batch: list[tuple[int, str, str, int, float]] = []
            for entry in entries:
                batch.append((scan_id, str(entry.path), entry.name, entry.size, entry.mtime))
                count += 1
                if len(batch) >= 10_000:
                    self._conn.executemany(
                        "INSERT INTO files (scan_id, path, name, size, mtime) "
                        "VALUES (?, ?, ?, ?, ?)",
                        batch,
                    )
                    batch.clear()
            if batch:
                self._conn.executemany(
                    "INSERT INTO files (scan_id, path, name, size, mtime) VALUES (?, ?, ?, ?, ?)",
                    batch,
                )

        return Scan(
            name=name,
            roots=root_tuple,
            scanned_at=scanned_at,
            use_mtime=use_mtime,
            skip_extensions=skip_extensions,
            file_count=count,
        )

    def _row_to_scan(self, row: sqlite3.Row) -> Scan:
        scan_id = int(row["id"])
        roots = tuple(
            Path(r["path"])
            for r in self._conn.execute(
                "SELECT path FROM scan_roots WHERE scan_id = ? ORDER BY rowid", (scan_id,)
            )
        )
        count = int(
            self._conn.execute(
                "SELECT COUNT(*) AS n FROM files WHERE scan_id = ?", (scan_id,)
            ).fetchone()["n"]
        )
        return Scan(
            name=row["name"],
            roots=roots,
            scanned_at=datetime.fromisoformat(row["scanned_at"]),
            use_mtime=bool(row["use_mtime"]),
            skip_extensions=frozenset(json.loads(row["skip_extensions"])),
            file_count=count,
        )

    def get_scan(self, name: str) -> Scan | None:
        row = self._conn.execute(
            "SELECT id, name, scanned_at, use_mtime, skip_extensions FROM scans WHERE name = ?",
            (name,),
        ).fetchone()
        return None if row is None else self._row_to_scan(row)

    def list_scans(self) -> list[Scan]:
        rows = self._conn.execute(
            "SELECT id, name, scanned_at, use_mtime, skip_extensions FROM scans ORDER BY name"
        ).fetchall()
        return [self._row_to_scan(row) for row in rows]

    def delete_scan(self, name: str) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM scans WHERE name = ?", (name,))
        return cursor.rowcount > 0

    def lookup_for(self, name: str) -> ScanLookup:
        return ScanLookup(self._conn, self._scan_id(name))
