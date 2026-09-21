"""The resident verify loop: pick a scan once, then work through cards.

Every collaborator that would make this untestable is injected — the detector,
the sleep, and both prompts — so a multi-card session runs deterministically
in a unit test with no terminal and no real time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from ftv.core.deephash import DeepScope
from ftv.core.models import Lookup, Scan
from ftv.core.verify import suggests_disabling_mtime, verify_card
from ftv.render import render_card_header, render_report, report_to_dict
from ftv.volumes import Volume, VolumeDetector, wait_for_new_volume

DEFAULT_WAIT_POLLS = 600


@dataclass(frozen=True, slots=True)
class SessionResult:
    cards_verified: int
    cards_safe: int
    exit_code: int


def run_session(
    *,
    scan: Scan,
    lookup: Lookup,
    detector: VolumeDetector,
    console: Console,
    confirm_card: Callable[[Volume], bool],
    ask_continue: Callable[[], bool],
    deep: DeepScope = DeepScope.NONE,
    skip_override: frozenset[str] | None = None,
    as_json: bool = False,
    poll_interval: float = 1.0,
    sleeper: Callable[[float], None] | None = None,
    wait_polls: int | None = DEFAULT_WAIT_POLLS,
) -> SessionResult:
    """Verify cards until the user stops or nothing new appears.

    Volumes already mounted at start are offered immediately, so launching with
    the card already inserted does not wait. A volume that is ejected drops out
    of the seen set, so re-inserting it is treated as a new card.
    """
    import time

    sleep = sleeper if sleeper is not None else time.sleep
    seen: set[Path] = set()
    verified = 0
    safe = 0

    while True:
        # wait_for_new_volume prunes ejected volumes from `seen` as it polls,
        # so the detector is queried exactly once per cycle.
        volume = wait_for_new_volume(
            detector,
            seen=seen,
            poll_interval=poll_interval,
            sleeper=sleep,
            stop_after=wait_polls,
        )
        if volume is None:
            break

        seen.add(volume.path)

        if not confirm_card(volume):
            continue

        if not as_json:
            render_card_header(console, scan, volume)

        report = verify_card(
            volume.path, scan, lookup, deep=deep, skip_override=skip_override
        )
        verified += 1
        if report.safe_to_format:
            safe += 1

        if as_json:
            console.print_json(json.dumps(report_to_dict(report)))
        else:
            render_report(
                console, report, scan, suggest_no_mtime=suggests_disabling_mtime(report)
            )

        if not ask_continue():
            break

    return SessionResult(
        cards_verified=verified,
        cards_safe=safe,
        exit_code=0 if verified == safe else 1,
    )
