"""Optional content verification for matched pairs.

Name-and-size matching cannot see a same-size collision, and the mtime signal
only flags it. Hashing settles it. The default scope is the suspicious pairs
alone, because that is where the ambiguity is and it is usually a handful of
files; hashing every match reads both copies of everything and costs minutes
per card.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from enum import Enum
from pathlib import Path

from ftv.core import fsread
from ftv.core.models import Bucket, Verdict

DEFAULT_CHUNK_SIZE = 1 << 20

HashFn = Callable[..., str]


class DeepScope(Enum):
    """Which matched pairs to content-verify."""

    NONE = "none"
    SUSPICIOUS = "suspicious"
    ALL = "all"

    @classmethod
    def from_flag(cls, value: str | None) -> DeepScope:
        """Parse the CLI spellings: absent, bare ``--deep``, or ``--deep=all``."""
        if value is None:
            return cls.NONE
        normalised = value.strip().lower()
        if normalised in ("", "suspicious"):
            return cls.SUSPICIOUS
        if normalised == "all":
            return cls.ALL
        raise ValueError(f"unknown --deep value {value!r}; use 'suspicious' or 'all'")


def hash_file(path: Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Streamed blake2b digest of ``path``."""
    digest = hashlib.blake2b(digest_size=32)
    for chunk in fsread.read_chunks(path, chunk_size=chunk_size):
        digest.update(chunk)
    return digest.hexdigest()


def _targets(scope: DeepScope) -> frozenset[Bucket]:
    if scope is DeepScope.SUSPICIOUS:
        return frozenset({Bucket.SUSPICIOUS})
    return frozenset({Bucket.SUSPICIOUS, Bucket.PRESENT})


def resolve(
    verdicts: Iterable[Verdict],
    scope: DeepScope,
    *,
    hasher: HashFn = hash_file,
) -> tuple[Verdict, ...]:
    """Content-verify the matched pairs in ``scope`` and re-bucket them.

    A pair whose contents agree becomes PRESENT; one whose contents differ
    becomes MISSING, because the destination holds a different file under that
    name. A pair that cannot be read stays SUSPICIOUS: an unverifiable file
    must never be promoted to safe.
    """
    if scope is DeepScope.NONE:
        return tuple(verdicts)

    targets = _targets(scope)
    out: list[Verdict] = []

    for verdict in verdicts:
        if verdict.matched is None or verdict.bucket not in targets:
            out.append(verdict)
            continue

        try:
            card_digest = hasher(verdict.entry.path)
            ssd_digest = hasher(Path(verdict.matched.path))
        except OSError as exc:
            out.append(
                Verdict(
                    entry=verdict.entry,
                    bucket=Bucket.SUSPICIOUS,
                    matched=verdict.matched,
                    reason=f"could not hash both copies: {exc.strerror or exc}",
                )
            )
            continue

        if card_digest == ssd_digest:
            out.append(
                Verdict(
                    entry=verdict.entry,
                    bucket=Bucket.PRESENT,
                    matched=verdict.matched,
                    reason="content verified identical",
                )
            )
        else:
            out.append(
                Verdict(
                    entry=verdict.entry,
                    bucket=Bucket.MISSING,
                    matched=verdict.matched,
                    reason="content differs from the file of that name on the destination",
                )
            )

    return tuple(out)
