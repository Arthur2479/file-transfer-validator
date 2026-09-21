import os
import sys
from pathlib import Path

import pytest

from ftv.volumes import (
    FakeVolumeDetector,
    MacOSVolumeDetector,
    Volume,
    VolumeDetector,
    wait_for_new_volume,
)


def test_fake_detector_satisfies_the_protocol():
    detector = FakeVolumeDetector([[]])
    assert isinstance(detector, VolumeDetector)


def test_fake_detector_replays_scripted_states():
    card = Volume(path=Path("/Volumes/NIKON Z8"), name="NIKON Z8", total_bytes=1000)
    detector = FakeVolumeDetector([[], [card], []])

    assert detector.list_removable() == []
    assert detector.list_removable() == [card]
    assert detector.list_removable() == []


def test_fake_detector_repeats_its_final_state_forever():
    card = Volume(path=Path("/Volumes/CARD"), name="CARD", total_bytes=1)
    detector = FakeVolumeDetector([[card]])
    assert detector.list_removable() == [card]
    assert detector.list_removable() == [card]


def test_wait_returns_a_volume_that_was_not_already_seen():
    card = Volume(path=Path("/Volumes/CARD"), name="CARD", total_bytes=1)
    detector = FakeVolumeDetector([[], [], [card]])
    slept: list[float] = []

    found = wait_for_new_volume(detector, seen=set(), poll_interval=0.5, sleeper=slept.append)

    assert found == card
    assert slept == [0.5, 0.5], "polls until something new appears"


def test_wait_ignores_a_volume_that_was_already_mounted():
    already = Volume(path=Path("/Volumes/TIME MACHINE"), name="TIME MACHINE", total_bytes=1)
    card = Volume(path=Path("/Volumes/CARD"), name="CARD", total_bytes=1)
    detector = FakeVolumeDetector([[already], [already, card]])

    found = wait_for_new_volume(
        detector, seen={already.path}, poll_interval=0, sleeper=lambda _: None
    )
    assert found == card


def test_wait_forgets_a_volume_that_was_ejected():
    """Re-inserting a card must offer it again."""
    card = Volume(path=Path("/Volumes/CARD"), name="CARD", total_bytes=1)
    detector = FakeVolumeDetector([[], [card]])
    seen = {card.path}

    found = wait_for_new_volume(detector, seen=seen, poll_interval=0, sleeper=lambda _: None)

    assert found == card
    assert seen == set(), "the ejected volume was pruned from the caller's set"


def test_wait_gives_up_after_the_configured_number_of_polls():
    detector = FakeVolumeDetector([[]])
    found = wait_for_new_volume(
        detector, seen=set(), poll_interval=0, sleeper=lambda _: None, stop_after=3
    )
    assert found is None


def test_wait_polls_the_detector_once_per_cycle():
    """Extra polls desynchronise a scripted fake and a real mount table alike."""
    detector = FakeVolumeDetector([[]])
    wait_for_new_volume(detector, seen=set(), poll_interval=0, sleeper=lambda _: None, stop_after=3)
    assert detector.calls == 3


def test_wait_returns_the_first_new_volume_when_several_appear_at_once():
    a = Volume(path=Path("/Volumes/A"), name="A", total_bytes=1)
    b = Volume(path=Path("/Volumes/B"), name="B", total_bytes=1)
    detector = FakeVolumeDetector([[b, a]])

    found = wait_for_new_volume(detector, seen=set(), poll_interval=0, sleeper=lambda _: None)
    assert found in (a, b)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS mount layout")
def test_macos_detector_returns_volumes_without_raising():
    volumes = MacOSVolumeDetector().list_removable()
    assert isinstance(volumes, list)
    assert all(isinstance(v, Volume) for v in volumes)
    assert all(v.path.is_dir() for v in volumes)


def test_macos_detector_excludes_the_boot_volume(tmp_path: Path, monkeypatch):
    """The boot volume shares its device with /, so it must never be offered."""
    fake_volumes = tmp_path / "Volumes"
    fake_volumes.mkdir()
    (fake_volumes / "Macintosh HD").mkdir()

    detector = MacOSVolumeDetector(mount_root=fake_volumes)
    root_dev = os.stat(os.sep).st_dev
    monkeypatch.setattr(
        "ftv.volumes.os.stat",
        lambda path, *a, **k: type("S", (), {"st_dev": root_dev})(),
    )

    assert detector.list_removable() == []


def test_macos_detector_returns_an_empty_list_when_the_mount_root_is_absent(tmp_path: Path):
    detector = MacOSVolumeDetector(mount_root=tmp_path / "no-such-dir")
    assert detector.list_removable() == []


def test_macos_detector_skips_symlinked_mount_entries(tmp_path: Path):
    fake_volumes = tmp_path / "Volumes"
    fake_volumes.mkdir()
    real = tmp_path / "elsewhere"
    real.mkdir()
    (fake_volumes / "Macintosh HD").symlink_to(real)

    detector = MacOSVolumeDetector(mount_root=fake_volumes)
    assert [v.name for v in detector.list_removable()] == []
