"""Re-checks matched files against the destination's current state.

A remembered scan can go stale in two directions, and only one is dangerous:
files deleted from the destination after scanning, where the index still claims
they are present. This module closes that direction by stat-ing each matched
path before the tool will call a card safe.

This is not a re-scan. It is one stat per matched file on a path the index
already named, which is what keeps the "many cards in a row" workflow fast.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from ftv.core import fsread
from ftv.core.models import Bucket, Verdict

StatFn = Callable[[Path], tuple[int, float]]


def confirm(
    verdicts: Iterable[Verdict],
    *,
    stat_file: StatFn = fsread.stat_file,
) -> tuple[Verdict, ...]:
    """Downgrade any PRESENT verdict whose destination file is no longer there.

    Only PRESENT verdicts are checked: a missing file has no destination path,
    and an already-suspicious verdict keeps its original reason rather than
    having it overwritten.
    """
    out: list[Verdict] = []

    for verdict in verdicts:
        if verdict.bucket is not Bucket.PRESENT or verdict.matched is None:
            out.append(verdict)
            continue

        try:
            size, _mtime = stat_file(Path(verdict.matched.path))
        except OSError as exc:
            out.append(
                Verdict(
                    entry=verdict.entry,
                    bucket=Bucket.SUSPICIOUS,
                    matched=verdict.matched,
                    reason=f"no longer readable on the destination: {exc.strerror or exc}",
                )
            )
            continue

        if size != verdict.matched.size:
            out.append(
                Verdict(
                    entry=verdict.entry,
                    bucket=Bucket.SUSPICIOUS,
                    matched=verdict.matched,
                    reason=(
                        f"size on the destination changed since the scan "
                        f"({verdict.matched.size} to {size} bytes)"
                    ),
                )
            )
            continue

        out.append(verdict)

    return tuple(out)
