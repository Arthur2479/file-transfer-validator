"""Decides which card files the tool holds accountable.

Deliberately a denylist, not an allowlist: a file type nobody configured must
still be checked, because an unchecked file type is a silent path to a wrong
"safe to format" verdict.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from pathlib import PurePosixPath


class Decision(Enum):
    KEEP = "keep"
    SKIP_USER = "skip_user"
    SKIP_JUNK = "skip_junk"


#: Directories that hold camera or OS bookkeeping, never captured media.
JUNK_DIRS = frozenset(
    {
        ".fseventsd",
        ".spotlight-v100",
        ".temporaryitems",
        ".trashes",
        "misc",
        "system volume information",
    }
)

#: Exact filenames that are never captured media.
JUNK_NAMES = frozenset({".ds_store", "autprint.mrk", "desktop.ini", "thumbs.db"})

#: Extensions that are camera bookkeeping (thumbnails, catalog files).
JUNK_SUFFIXES = frozenset({".thm", ".ctg", ".mrk", ".bin", ".modd", ".moff"})


def normalize_extension(value: str) -> str:
    """Normalise an extension as typed into ``.ext`` lowercase form."""
    cleaned = value.strip().lstrip("*").strip()
    if cleaned.startswith("."):
        cleaned = cleaned[1:]
    cleaned = cleaned.strip().lower()
    if not cleaned:
        raise ValueError("extension is empty")
    return f".{cleaned}"


def normalize_skip_list(values: Iterable[str] | None) -> frozenset[str]:
    """Normalise a skip list, accepting repeated flags and comma-separated values."""
    if not values:
        return frozenset()
    out: set[str] = set()
    for value in values:
        for part in value.split(","):
            if part.strip():
                out.add(normalize_extension(part))
    return frozenset(out)


def is_junk_dir(name: str) -> bool:
    """True when a directory basename should be pruned from the walk."""
    return name.lower() in JUNK_DIRS


def decide(relpath: str, skip_extensions: frozenset[str]) -> Decision:
    """Classify a card-relative path.

    Junk is checked before the user skip list so that reporting never credits
    camera cruft to a deliberate user decision.
    """
    parts = PurePosixPath(relpath).parts
    if any(is_junk_dir(part) for part in parts[:-1]):
        return Decision.SKIP_JUNK

    name = parts[-1] if parts else relpath
    lowered = name.lower()
    if lowered in JUNK_NAMES or lowered.startswith("._"):
        return Decision.SKIP_JUNK

    suffix = PurePosixPath(lowered).suffix
    if suffix and suffix in JUNK_SUFFIXES:
        return Decision.SKIP_JUNK
    if suffix and suffix in skip_extensions:
        return Decision.SKIP_USER
    return Decision.KEEP
