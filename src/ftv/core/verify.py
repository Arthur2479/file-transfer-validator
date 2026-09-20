"""Turns a mounted card into a VerifyReport.

One sequence: walk the card, compare against the scan, confirm the matches are
still on the destination, then optionally hash. Kept separate from the terminal
session so the whole pipeline is testable without any UI.
"""

from __future__ import annotations

from pathlib import Path

from ftv.core.compare import compare, timestamps_look_unpreserved
from ftv.core.confirm import confirm
from ftv.core.deephash import DeepScope, resolve
from ftv.core.models import Lookup, Scan, VerifyReport
from ftv.core.walker import walk_tree


class DestinationUnavailableError(Exception):
    """Raised when a scan's destination roots are not currently mounted."""


def check_destination_available(scan: Scan) -> None:
    """Raise unless every root of ``scan`` is present.

    Without this, an unplugged destination would fail every confirmation and
    turn a perfectly good card into a wall of suspicious files. Refusing is the
    honest answer: the tool cannot verify anything it cannot reach.
    """
    absent = [str(root) for root in scan.roots if not root.is_dir()]
    if absent:
        raise DestinationUnavailableError(
            "these destination roots are not available: " + ", ".join(absent)
        )


def verify_card(
    card_root: Path,
    scan: Scan,
    lookup: Lookup,
    *,
    deep: DeepScope = DeepScope.NONE,
    skip_override: frozenset[str] | None = None,
) -> VerifyReport:
    """Verify one card against one remembered scan."""
    skips = scan.skip_extensions if skip_override is None else skip_override

    outcome = walk_tree(card_root, skips)
    verdicts = compare(outcome.entries, lookup, use_mtime=scan.use_mtime)
    verdicts = confirm(verdicts)
    verdicts = resolve(verdicts, deep)

    return VerifyReport(
        scan_name=scan.name,
        card_root=card_root,
        verdicts=verdicts,
        skipped_user=outcome.skipped_user,
        skipped_junk=outcome.skipped_junk,
        errors=outcome.errors,
    )


def suggests_disabling_mtime(report: VerifyReport) -> bool:
    """True when the report looks like a copy tool that discarded timestamps."""
    return timestamps_look_unpreserved(report.verdicts)
