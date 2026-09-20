"""Turns a directory tree into classified file entries.

Used for both sides of the comparison: to build a scan of a destination, and
to enumerate a card at verify time. Sharing this module is what guarantees both
sides apply an identical notion of which files count.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ftv.core import filters, fsread
from ftv.core.filters import Decision
from ftv.core.fsread import walk as _fsread_walk
from ftv.core.models import FileEntry, WalkError


@dataclass(frozen=True, slots=True)
class WalkOutcome:
    """Every file under a root, sorted into what gets checked and what does not."""

    entries: tuple[FileEntry, ...] = ()
    skipped_user: tuple[str, ...] = ()
    skipped_junk: tuple[str, ...] = ()
    errors: tuple[WalkError, ...] = ()

    @property
    def total_files(self) -> int:
        return (
            len(self.entries)
            + len(self.skipped_user)
            + len(self.skipped_junk)
            + len(self.errors)
        )


def walk_tree(root: Path, skip_extensions: frozenset[str]) -> WalkOutcome:
    """Walk ``root`` and classify every file found.

    Junk directories are pruned during the walk rather than filtered afterwards,
    so a large ``System Volume Information`` tree costs nothing. Files that
    cannot be stat'ed become errors instead of vanishing, because a file the
    tool could not measure must never be implied to be backed up.
    """
    entries: list[FileEntry] = []
    skipped_user: list[str] = []
    skipped_junk: list[str] = []
    errors: list[WalkError] = []

    for found in _fsread_walk(root, prune=filters.is_junk_dir):
        if isinstance(found, WalkError):
            errors.append(found)
            continue

        decision = filters.decide(found.relpath, skip_extensions)
        if decision is Decision.SKIP_JUNK:
            skipped_junk.append(found.relpath)
            continue
        if decision is Decision.SKIP_USER:
            skipped_user.append(found.relpath)
            continue

        try:
            size, mtime = fsread.stat_file(found.path)
        except OSError as exc:
            errors.append(
                WalkError(path=found.path, message=str(exc.strerror or exc))
            )
            continue

        entries.append(
            FileEntry(
                path=found.path,
                relpath=found.relpath,
                name=found.path.name,
                size=size,
                mtime=mtime,
            )
        )

    return WalkOutcome(
        entries=tuple(entries),
        skipped_user=tuple(skipped_user),
        skipped_junk=tuple(skipped_junk),
        errors=tuple(errors),
    )
