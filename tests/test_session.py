from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console

from ftv.core.deephash import DeepScope
from ftv.core.models import IndexedFile, Scan
from ftv.session import SessionResult, run_session
from ftv.volumes import FakeVolumeDetector, Volume


class FakeLookup:
    def __init__(self, files: list[IndexedFile] | None = None) -> None:
        self.files = files or []

    def find(self, name: str, size: int) -> list[IndexedFile]:
        return [f for f in self.files if f.name == name and f.size == size]


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    dest = tmp_path / "ssd"
    dest.mkdir()
    return dest


def make_scan(destination: Path) -> Scan:
    return Scan(
        name="Video SSD",
        roots=(destination,),
        scanned_at=datetime(2026, 9, 14, tzinfo=UTC),
        use_mtime=True,
    )


def make_card(tmp_path: Path, label: str, names: list[str]) -> Volume:
    root = tmp_path / label
    (root / "DCIM").mkdir(parents=True)
    for name in names:
        (root / "DCIM" / name).write_bytes(name.encode() * 4)
    return Volume(path=root, name=label, total_bytes=1_000_000)


def index_card(destination: Path, card: Volume) -> list[IndexedFile]:
    """Mirror every file on the card into the destination and index it."""
    import os

    out = []
    for source in sorted((card.path / "DCIM").iterdir()):
        target = destination / source.name
        target.write_bytes(source.read_bytes())
        stat = source.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime))
        out.append(
            IndexedFile(path=str(target), name=source.name, size=stat.st_size, mtime=stat.st_mtime)
        )
    return out


def console() -> Console:
    return Console(record=True, width=100, force_terminal=False, no_color=True)


def run(detector, scan, lookup, con, **kwargs) -> SessionResult:
    """Drive a session with every real-world collaborator replaced.

    `wait_polls=3` bounds the wait so a test can never hang, and the scripted
    detector advances exactly one state per poll — which only holds because the
    session polls the detector once per cycle and never out of band.
    """
    defaults = dict(
        confirm_card=lambda volume: True,
        ask_continue=lambda: False,
        sleeper=lambda _: None,
        poll_interval=0,
        wait_polls=3,
    )
    defaults.update(kwargs)
    return run_session(scan=scan, lookup=lookup, detector=detector, console=con, **defaults)


def test_one_fully_transferred_card_is_reported_safe(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "NIKON Z8", ["A.JPG", "B.MP4"])
    lookup = FakeLookup(index_card(destination, card))
    con = console()

    result = run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, con)

    assert result.cards_verified == 1
    assert result.cards_safe == 1
    assert result.exit_code == 0
    assert "safe to format" in con.export_text().lower()


def test_a_card_with_a_missing_file_sets_a_failing_exit_code(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "SONY A7", ["A.JPG", "B.MP4"])
    indexed = index_card(destination, card)
    lookup = FakeLookup(indexed[:1])
    con = console()

    result = run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, con)

    assert result.cards_verified == 1
    assert result.cards_safe == 0
    assert result.exit_code == 1
    assert "B.MP4" in con.export_text()


def test_several_cards_run_one_after_another(tmp_path: Path, destination: Path):
    first = make_card(tmp_path, "CARD1", ["A.JPG"])
    second = make_card(tmp_path, "CARD2", ["B.JPG"])
    third = make_card(tmp_path, "CARD3", ["C.JPG"])
    lookup = FakeLookup(
        index_card(destination, first)
        + index_card(destination, second)
        + index_card(destination, third)
    )
    detector = FakeVolumeDetector([[first], [second], [third]])
    answers = iter([True, True, False])
    con = console()

    result = run(
        detector, make_scan(destination), lookup, con, ask_continue=lambda: next(answers)
    )

    assert result.cards_verified == 3
    assert result.cards_safe == 3
    assert result.exit_code == 0


def test_a_failing_card_makes_the_whole_session_fail(tmp_path: Path, destination: Path):
    good = make_card(tmp_path, "GOOD", ["A.JPG"])
    bad = make_card(tmp_path, "BAD", ["B.JPG"])
    lookup = FakeLookup(index_card(destination, good))
    detector = FakeVolumeDetector([[good], [bad]])
    answers = iter([True, False])
    con = console()

    result = run(
        detector, make_scan(destination), lookup, con, ask_continue=lambda: next(answers)
    )

    assert result.cards_verified == 2
    assert result.cards_safe == 1
    assert result.exit_code == 1, "one bad card must fail the session"


def test_declining_a_volume_skips_it_without_verifying(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "TIME MACHINE", ["A.JPG"])
    con = console()

    result = run(
        FakeVolumeDetector([[card]]),
        make_scan(destination),
        FakeLookup([]),
        con,
        confirm_card=lambda volume: False,
    )

    assert result.cards_verified == 0
    assert result.exit_code == 0, "skipping a drive is not a failure"


def test_a_declined_volume_is_not_offered_again(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "TIME MACHINE", ["A.JPG"])
    asked: list[str] = []

    def confirm(volume: Volume) -> bool:
        asked.append(volume.name)
        return False

    run(
        FakeVolumeDetector([[card]]),
        make_scan(destination),
        FakeLookup([]),
        console(),
        confirm_card=confirm,
    )

    assert asked == ["TIME MACHINE"], "the same drive must not be re-offered in a loop"


def test_the_confirmation_prompt_sees_the_volume_name_and_size(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "NIKON Z8", ["A.JPG"])
    seen: list[Volume] = []

    run(
        FakeVolumeDetector([[card]]),
        make_scan(destination),
        FakeLookup([]),
        console(),
        confirm_card=lambda volume: (seen.append(volume), False)[1],
    )

    assert seen[0].name == "NIKON Z8"
    assert seen[0].total_bytes == 1_000_000


def test_the_session_ends_when_no_card_appears(destination: Path):
    con = console()
    result = run(FakeVolumeDetector([[]]), make_scan(destination), FakeLookup([]), con)

    assert result.cards_verified == 0
    assert result.exit_code == 0


def test_an_already_mounted_card_is_offered_immediately(tmp_path: Path, destination: Path):
    """Launching with the card already in must not wait forever."""
    card = make_card(tmp_path, "ALREADY IN", ["A.JPG"])
    lookup = FakeLookup(index_card(destination, card))

    result = run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, console())
    assert result.cards_verified == 1


def test_a_card_reinserted_after_ejection_is_offered_again(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "CARD", ["A.JPG"])
    lookup = FakeLookup(index_card(destination, card))
    detector = FakeVolumeDetector([[card], [], [card]])
    answers = iter([True, False])

    result = run(
        detector, make_scan(destination), lookup, console(), ask_continue=lambda: next(answers)
    )
    assert result.cards_verified == 2


def test_deep_scope_is_passed_through_to_verification(tmp_path: Path, destination: Path):
    import os

    card = make_card(tmp_path, "CARD", ["A.JPG"])
    indexed = index_card(destination, card)
    # Same name, same size, same mtime, different bytes.
    Path(indexed[0].path).write_bytes(b"Z" * indexed[0].size)
    os.utime(indexed[0].path, (indexed[0].mtime, indexed[0].mtime))
    lookup = FakeLookup(indexed)

    shallow = run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, console())
    assert shallow.exit_code == 0

    deep = run(
        FakeVolumeDetector([[card]]),
        make_scan(destination),
        lookup,
        console(),
        deep=DeepScope.ALL,
    )
    assert deep.exit_code == 1


def test_the_card_header_is_printed_before_the_verdict(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "NIKON Z8", ["A.JPG"])
    lookup = FakeLookup(index_card(destination, card))
    con = console()

    run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, con)
    out = con.export_text()

    assert "NIKON Z8" in out
    assert out.index("NIKON Z8") < out.lower().index("safe to format")


def test_json_mode_prints_one_object_per_card_and_no_decoration(
    tmp_path: Path, destination: Path
):
    import json

    card = make_card(tmp_path, "CARD", ["A.JPG"])
    lookup = FakeLookup(index_card(destination, card))
    con = console()

    run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, con, as_json=True)
    out = con.export_text().strip()

    payload = json.loads(out)
    assert payload["safe_to_format"] is True
    assert "safe to format" not in out.lower()
