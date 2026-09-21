"""Immutable data types shared across the tool.

These types carry no behaviour that touches the filesystem, the database, or
the terminal, so every other module can depend on them freely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class Bucket(str, Enum):
    """The outcome for a single card file. Every card file lands in exactly one."""

    PRESENT = "present"
    SUSPICIOUS = "suspicious"
    MISSING = "missing"
    SKIPPED_USER = "skipped_user"
    SKIPPED_JUNK = "skipped_junk"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class FileEntry:
    """A file found on a card or in a destination root."""

    path: Path
    relpath: str
    name: str
    size: int
    mtime: float


@dataclass(frozen=True, slots=True)
class IndexedFile:
    """A file recorded in a remembered scan of a destination."""

    path: str
    name: str
    size: int
    mtime: float


@dataclass(frozen=True, slots=True)
class WalkError:
    """A path that could not be read or stat'ed."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class Verdict:
    """The comparison outcome for one card file that was actually checked."""

    entry: FileEntry
    bucket: Bucket
    matched: IndexedFile | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Scan:
    """A remembered scan of one or more destination roots."""

    name: str
    roots: tuple[Path, ...]
    scanned_at: datetime
    use_mtime: bool = True
    skip_extensions: frozenset[str] = frozenset()
    file_count: int = 0


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """The full outcome of checking one card against one scan.

    ``verdicts`` holds the files that were actually compared. Skipped files and
    errors get dedicated fields because they carry no match and may have no
    stat, but ``counts()`` still reports all six buckets so the "exactly one
    bucket per file" invariant stays checkable.
    """

    scan_name: str
    card_root: Path
    verdicts: tuple[Verdict, ...] = ()
    skipped_user: tuple[str, ...] = ()
    skipped_junk: tuple[str, ...] = ()
    errors: tuple[WalkError, ...] = ()

    def of(self, bucket: Bucket) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.bucket is bucket)

    @property
    def present(self) -> tuple[Verdict, ...]:
        return self.of(Bucket.PRESENT)

    @property
    def suspicious(self) -> tuple[Verdict, ...]:
        return self.of(Bucket.SUSPICIOUS)

    @property
    def missing(self) -> tuple[Verdict, ...]:
        return self.of(Bucket.MISSING)

    @property
    def checked_count(self) -> int:
        return len(self.verdicts)

    @property
    def total_files(self) -> int:
        return (
            len(self.verdicts) + len(self.skipped_user) + len(self.skipped_junk) + len(self.errors)
        )

    def counts(self) -> dict[Bucket, int]:
        counts = {bucket: 0 for bucket in Bucket}
        for verdict in self.verdicts:
            counts[verdict.bucket] += 1
        counts[Bucket.SKIPPED_USER] = len(self.skipped_user)
        counts[Bucket.SKIPPED_JUNK] = len(self.skipped_junk)
        counts[Bucket.ERROR] = len(self.errors)
        return counts

    @property
    def safe_to_format(self) -> bool:
        """True only when nothing was left unresolved.

        Deliberately conservative: an unreadable file or an ambiguous match
        blocks the green verdict rather than being rounded down to acceptable.
        """
        return not (self.missing or self.suspicious or self.errors)

    @property
    def exit_code(self) -> int:
        return 0 if self.safe_to_format else 1


@runtime_checkable
class Lookup(Protocol):
    """Answers "is there a file with this basename and size in the scan?"."""

    def find(self, name: str, size: int) -> list[IndexedFile]: ...
