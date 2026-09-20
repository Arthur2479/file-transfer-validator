from datetime import UTC, datetime
from pathlib import Path

import pytest

from ftv.core.deephash import DeepScope
from ftv.core.models import Bucket, IndexedFile, Scan
from ftv.core.verify import (
    DestinationUnavailableError,
    check_destination_available,
    suggests_disabling_mtime,
    verify_card,
)


class FakeLookup:
    def __init__(self, files: list[IndexedFile] | None = None) -> None:
        self.files = files or []

    def find(self, name: str, size: int) -> list[IndexedFile]:
        return [f for f in self.files if f.name == name and f.size == size]


def make_scan(root: Path, *, use_mtime: bool = True, skips: frozenset[str] = frozenset()) -> Scan:
    return Scan(
        name="Video SSD",
        roots=(root,),
        scanned_at=datetime(2026, 9, 14, tzinfo=UTC),
        use_mtime=use_mtime,
        skip_extensions=skips,
    )


@pytest.fixture
def card(tmp_path: Path) -> Path:
    root = tmp_path / "card"
    (root / "DCIM" / "100NZ_8").mkdir(parents=True)
    (root / "DCIM" / "100NZ_8" / "DSC_0001.JPG").write_bytes(b"a" * 10)
    (root / "DCIM" / "100NZ_8" / "DSC_0002.JPG").write_bytes(b"b" * 20)
    (root / "DCIM" / "100NZ_8" / "DSC_0003.NEF").write_bytes(b"c" * 30)
    (root / ".DS_Store").write_bytes(b"junk")
    return root


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    dest = tmp_path / "ssd"
    dest.mkdir()
    return dest


def copy_into(destination: Path, card: Path, relpath: str) -> IndexedFile:
    """Mirror a card file into the destination under a reorganised name."""
    source = card / relpath
    target = destination / "2026" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    stat = source.stat()
    import os

    os.utime(target, (stat.st_atime, stat.st_mtime))
    return IndexedFile(path=str(target), name=source.name, size=stat.st_size, mtime=stat.st_mtime)


def test_a_fully_transferred_card_is_safe(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0003.NEF"),
    ]
    report = verify_card(card, make_scan(destination), FakeLookup(indexed))

    assert report.safe_to_format is True
    assert report.exit_code == 0
    assert len(report.present) == 3
    assert report.skipped_junk == (".DS_Store",)


def test_a_missing_file_blocks_the_verdict(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
    ]
    report = verify_card(card, make_scan(destination), FakeLookup(indexed))

    assert report.safe_to_format is False
    assert report.exit_code == 1
    assert [v.entry.name for v in report.missing] == ["DSC_0003.NEF"]


def test_a_skip_listed_extension_is_not_required(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
    ]
    scan = make_scan(destination, skips=frozenset({".nef"}))
    report = verify_card(card, scan, FakeLookup(indexed))

    assert report.safe_to_format is True
    assert report.skipped_user == ("DCIM/100NZ_8/DSC_0003.NEF",)


def test_a_per_run_skip_override_replaces_the_scans_skip_list(card: Path, destination: Path):
    indexed = [copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG")]
    scan = make_scan(destination, skips=frozenset({".nef"}))
    report = verify_card(
        card, scan, FakeLookup(indexed), skip_override=frozenset({".nef", ".jpg"})
    )

    assert report.safe_to_format is True
    assert len(report.skipped_user) == 3


def test_the_confirm_step_catches_a_file_deleted_after_the_scan(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0003.NEF"),
    ]
    Path(indexed[2].path).unlink()

    report = verify_card(card, make_scan(destination), FakeLookup(indexed))

    assert report.safe_to_format is False
    assert [v.entry.name for v in report.suspicious] == ["DSC_0003.NEF"]


def test_deep_all_catches_a_same_size_different_content_collision(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0003.NEF"),
    ]
    # Same name, same size, same mtime, different bytes: invisible without hashing.
    Path(indexed[0].path).write_bytes(b"z" * 10)
    import os

    os.utime(indexed[0].path, (indexed[0].mtime, indexed[0].mtime))

    shallow = verify_card(card, make_scan(destination), FakeLookup(indexed))
    assert shallow.safe_to_format is True, "name+size+mtime cannot see this"

    deep = verify_card(card, make_scan(destination), FakeLookup(indexed), deep=DeepScope.ALL)
    assert deep.safe_to_format is False
    assert [v.entry.name for v in deep.missing] == ["DSC_0001.JPG"]


def test_an_empty_card_is_reported_as_nothing_to_verify(tmp_path: Path, destination: Path):
    empty = tmp_path / "empty-card"
    empty.mkdir()
    report = verify_card(empty, make_scan(destination), FakeLookup([]))

    assert report.checked_count == 0
    assert report.total_files == 0
    assert report.safe_to_format is True, "an empty card has nothing unresolved"


def test_a_card_that_cannot_be_read_yields_an_error_not_a_green_verdict(
    tmp_path: Path, destination: Path
):
    report = verify_card(tmp_path / "unplugged", make_scan(destination), FakeLookup([]))

    assert len(report.errors) == 1
    assert report.safe_to_format is False


def test_the_report_records_the_scan_name_and_card_root(card: Path, destination: Path):
    report = verify_card(card, make_scan(destination), FakeLookup([]))
    assert report.scan_name == "Video SSD"
    assert report.card_root == card


def test_verifying_refuses_when_a_destination_root_is_not_mounted(tmp_path: Path):
    scan = make_scan(tmp_path / "unplugged-ssd")
    with pytest.raises(DestinationUnavailableError, match="unplugged-ssd"):
        check_destination_available(scan)


def test_destination_check_passes_when_every_root_is_present(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    scan = Scan(name="S", roots=(a, b), scanned_at=datetime(2026, 9, 14, tzinfo=UTC))
    check_destination_available(scan)


def test_destination_check_names_every_missing_root(tmp_path: Path):
    present_root = tmp_path / "here"
    present_root.mkdir()
    scan = Scan(
        name="S",
        roots=(present_root, tmp_path / "gone-one", tmp_path / "gone-two"),
        scanned_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    with pytest.raises(DestinationUnavailableError) as excinfo:
        check_destination_available(scan)
    assert "gone-one" in str(excinfo.value)
    assert "gone-two" in str(excinfo.value)


def test_a_wall_of_suspicious_matches_suggests_disabling_the_mtime_signal(
    tmp_path: Path, destination: Path
):
    card = tmp_path / "card"
    card.mkdir()
    indexed = []
    for n in range(15):
        source = card / f"IMG_{n:04}.JPG"
        source.write_bytes(bytes([n]) * (100 + n))
        target = destination / f"IMG_{n:04}.JPG"
        target.write_bytes(source.read_bytes())
        indexed.append(
            IndexedFile(
                path=str(target), name=target.name, size=100 + n, mtime=1.0
            )
        )

    report = verify_card(card, make_scan(destination), FakeLookup(indexed))
    assert len(report.suspicious) == 15
    assert suggests_disabling_mtime(report) is True


def test_no_suggestion_when_matches_are_clean(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0003.NEF"),
    ]
    report = verify_card(card, make_scan(destination), FakeLookup(indexed))
    assert suggests_disabling_mtime(report) is False
