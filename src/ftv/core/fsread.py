"""The only module permitted to touch card or destination paths.

Every filesystem operation the tool performs on user data goes through here,
and everything here is read-only: directory listing, stat, and reading bytes.
Concentrating access in one small module is what makes the read-only invariant
auditable — see tests/test_readonly_invariant.py.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ftv.core.models import WalkError

DEFAULT_CHUNK_SIZE = 1 << 20


@dataclass(frozen=True, slots=True)
class FoundFile:
    """A file located by :func:`walk`."""

    path: Path
    relpath: str


def stat_file(path: Path) -> tuple[int, float]:
    """Return ``(size, mtime)`` for ``path``. Raises OSError on failure."""
    st = path.stat()
    return st.st_size, st.st_mtime


def read_chunks(path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
    """Stream ``path`` in binary chunks.

    Opened with mode ``"rb"`` only; the audit test rejects any other mode
    anywhere in this package.
    """
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            yield chunk


def walk(
    root: Path,
    prune: Callable[[str], bool] | None = None,
) -> Iterator[FoundFile | WalkError]:
    """Yield every regular file under ``root``, plus a WalkError per failure.

    ``prune`` is called with each directory's basename; returning True skips
    that directory and everything beneath it. Symlinks are never followed, so
    a link loop cannot hang the walk and a link out of the tree cannot pull
    unrelated files into the comparison.

    Failures are yielded rather than raised: one unreadable directory must not
    discard the verdict for the rest of the card.
    """
    root = root.resolve()
    if not root.is_dir():
        yield WalkError(path=root, message="not a readable directory")
        return

    def on_error(exc: OSError) -> None:
        errors.append(WalkError(path=Path(exc.filename or root), message=str(exc.strerror or exc)))

    errors: list[WalkError] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if not (current / d).is_symlink() and not (prune(d) if prune else False)
        ]
        for filename in filenames:
            candidate = current / filename
            if candidate.is_symlink() or not candidate.is_file():
                continue
            yield FoundFile(path=candidate, relpath=candidate.relative_to(root).as_posix())
        while errors:
            yield errors.pop()
    while errors:
        yield errors.pop()
