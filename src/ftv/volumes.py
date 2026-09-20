"""Finding mounted volumes that might be a card.

This inspects the system's mount points rather than the user's files, so it
sits outside ``ftv.core`` and outside the read-only data-access boundary.

The tool cannot reliably tell an SD card from an external SSD without shelling
out to platform tools, and it does not need to: every card is confirmed with
the user before being verified, and verification is read-only, so the cost of
offering the wrong drive is a wasted scan rather than any risk to data.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

DEFAULT_POLL_INTERVAL = 1.0


@dataclass(frozen=True, slots=True)
class Volume:
    """A mounted volume that could hold card files."""

    path: Path
    name: str
    total_bytes: int


@runtime_checkable
class VolumeDetector(Protocol):
    """Lists volumes that are candidates for being a card."""

    def list_removable(self) -> list[Volume]: ...


class MacOSVolumeDetector:
    """Lists non-boot volumes mounted under ``/Volumes``.

    The boot volume is excluded by comparing device ids against ``/``, which is
    more robust than matching its name. Symlinked entries are skipped: macOS
    represents the boot volume that way.
    """

    __slots__ = ("_mount_root",)

    def __init__(self, mount_root: Path | str = "/Volumes") -> None:
        self._mount_root = Path(mount_root)

    def list_removable(self) -> list[Volume]:
        try:
            root_device = os.stat(os.sep).st_dev
        except OSError:
            root_device = -1

        volumes: list[Volume] = []
        try:
            names = sorted(os.listdir(self._mount_root))
        except OSError:
            return []

        for name in names:
            candidate = self._mount_root / name
            try:
                if candidate.is_symlink():
                    continue
                if os.stat(candidate).st_dev == root_device:
                    continue
                if not candidate.is_dir():
                    continue
                usage = os.statvfs(candidate)
            except OSError:
                continue
            volumes.append(
                Volume(
                    path=candidate,
                    name=name,
                    total_bytes=usage.f_blocks * usage.f_frsize,
                )
            )
        return volumes


class FakeVolumeDetector:
    """Replays a scripted sequence of mount states.

    Lives in the production module because the session tests need it too: a
    scripted detector is the only way to drive the card loop deterministically.
    The final state repeats forever, so a loop cannot run off the end.
    """

    __slots__ = ("_states", "_calls")

    def __init__(self, states: Sequence[Sequence[Volume]]) -> None:
        if not states:
            raise ValueError("at least one state is required")
        self._states = [list(state) for state in states]
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def list_removable(self) -> list[Volume]:
        index = min(self._calls, len(self._states) - 1)
        self._calls += 1
        return list(self._states[index])


def wait_for_new_volume(
    detector: VolumeDetector,
    *,
    seen: set[Path],
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    sleeper: Callable[[float], None] = time.sleep,
    stop_after: int | None = None,
) -> Volume | None:
    """Poll until a volume appears that is not in ``seen``.

    ``seen`` is MUTATED: volumes that have been ejected are removed from it, so
    re-inserting a card offers it again. The caller adds a volume to ``seen``
    once it has been handled. Doing the pruning here rather than in the caller
    means the detector is polled exactly once per cycle, which matters both for
    real mount tables and for scripted fakes in tests.

    ``sleeper`` is injected so the loop is testable without real time. Returns
    None when ``stop_after`` polls elapse with nothing new, which is what lets
    a caller offer a way out of the wait.
    """
    polls = 0

    while stop_after is None or polls < stop_after:
        current = detector.list_removable()
        seen &= {volume.path for volume in current}

        for volume in current:
            if volume.path not in seen:
                return volume

        polls += 1
        if stop_after is not None and polls >= stop_after:
            break
        sleeper(poll_interval)

    return None
