"""Where the index database may and may not live.

The index is the only thing this tool writes. Letting it land on a card or
inside a destination would violate the read-only invariant, so the location is
validated rather than trusted.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "ftv"

#: Mount points where removable media appears. macOS first; extend per platform.
REMOVABLE_PREFIXES: tuple[str, ...] = ("/Volumes", "/media", "/run/media", "/mnt")


class DbPathError(Exception):
    """Raised when the index database would be written somewhere unsafe."""


def default_db_path() -> Path:
    return Path(user_data_dir(APP_NAME)) / "index.db"


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_db_path(db_path: Path, roots: Iterable[Path]) -> None:
    """Raise :class:`DbPathError` unless ``db_path`` is a safe place to write.

    Rejects a database inside any scan root, and a database on a removable
    volume: either would mean writing to the very data the tool promises never
    to modify.
    """
    resolved = Path(db_path).expanduser().resolve()

    for prefix in REMOVABLE_PREFIXES:
        prefix_path = Path(prefix)
        if resolved != prefix_path and _is_within(resolved, prefix_path):
            raise DbPathError(
                f"refusing to write the index to {resolved}: that is a removable volume"
            )

    for root in roots:
        try:
            root_resolved = Path(root).expanduser().resolve()
        except OSError:
            continue
        if _is_within(resolved, root_resolved):
            raise DbPathError(
                f"refusing to write the index to {resolved}: that is inside a scan root "
                f"({root_resolved})"
            )
