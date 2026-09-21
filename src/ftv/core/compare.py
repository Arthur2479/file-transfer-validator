"""The comparison core: card files plus a Lookup, in; verdicts, out.

Pure by design. No filesystem, no database, no terminal. This is the code whose
correctness decides whether a card gets formatted, so it is kept small enough
to read in one sitting and testable with a fake Lookup.
"""

from __future__ import annotations

from collections.abc import Iterable

from ftv.core.models import Bucket, FileEntry, IndexedFile, Lookup, Verdict

#: FAT and exFAT store modification times at two-second granularity, so a
#: byte-identical copy on an APFS destination can legitimately differ by up to
#: two seconds. Anything wider than this is a genuine signal, not filesystem
#: rounding.
MTIME_TOLERANCE_SECONDS = 2.0

#: Below this many matched files, "every match is suspicious" is more likely a
#: real collision than a copy tool that drops timestamps.
UNPRESERVED_MINIMUM = 10


def _best_by_mtime(
    entry: FileEntry, candidates: list[IndexedFile], tolerance: float
) -> IndexedFile | None:
    """Return the candidate whose mtime matches ``entry`` most closely, if any."""
    within = [c for c in candidates if abs(c.mtime - entry.mtime) <= tolerance]
    if not within:
        return None
    return min(within, key=lambda c: abs(c.mtime - entry.mtime))


def compare(
    entries: Iterable[FileEntry],
    lookup: Lookup,
    *,
    use_mtime: bool = True,
    mtime_tolerance: float = MTIME_TOLERANCE_SECONDS,
) -> tuple[Verdict, ...]:
    """Classify every card entry as present, suspicious, or missing.

    Matching is path-agnostic: basename plus byte size, so reorganising the
    destination never invalidates a scan. When ``use_mtime`` is set, a
    name-and-size match whose modification time also agrees is accepted; a
    match whose time disagrees is reported suspicious rather than passed,
    because two cards from the same camera hold different photographs under
    identical filenames.
    """
    verdicts: list[Verdict] = []

    for entry in entries:
        candidates = lookup.find(entry.name, entry.size)

        if not candidates:
            verdicts.append(Verdict(entry=entry, bucket=Bucket.MISSING))
            continue

        if not use_mtime:
            verdicts.append(Verdict(entry=entry, bucket=Bucket.PRESENT, matched=candidates[0]))
            continue

        best = _best_by_mtime(entry, candidates, mtime_tolerance)
        if best is not None:
            verdicts.append(Verdict(entry=entry, bucket=Bucket.PRESENT, matched=best))
            continue

        nearest = min(candidates, key=lambda c: abs(c.mtime - entry.mtime))
        verdicts.append(
            Verdict(
                entry=entry,
                bucket=Bucket.SUSPICIOUS,
                matched=nearest,
                reason="same name and size but a different capture time",
            )
        )

    return tuple(verdicts)


def timestamps_look_unpreserved(
    verdicts: Iterable[Verdict], *, minimum: int = UNPRESERVED_MINIMUM
) -> bool:
    """True when the results look like a copy tool that discarded timestamps.

    That case produces a wall of suspicious matches and no clean ones. Detecting
    it lets the report suggest turning the signal off for this scan instead of
    listing thousands of warnings.
    """
    matched = [v for v in verdicts if v.matched is not None]
    if len(matched) < minimum:
        return False
    return all(v.bucket is Bucket.SUSPICIOUS for v in matched)
