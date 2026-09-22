import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ftv.core.models import (
    Bucket,
    FileEntry,
    IndexedFile,
    Scan,
    Verdict,
    VerifyReport,
    WalkError,
)


def entry(name: str = "IMG_0001.JPG", size: int = 100, mtime: float = 1000.0) -> FileEntry:
    return FileEntry(
        path=Path("/Volumes/CARD/DCIM/100NZ_8") / name,
        relpath=f"DCIM/100NZ_8/{name}",
        name=name,
        size=size,
        mtime=mtime,
    )


def test_file_entry_is_frozen():
    e = entry()
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.size = 1  # type: ignore[misc]


def test_report_groups_verdicts_by_bucket():
    present = Verdict(entry=entry("A.JPG"), bucket=Bucket.PRESENT)
    missing = Verdict(entry=entry("B.JPG"), bucket=Bucket.MISSING)
    suspicious = Verdict(entry=entry("C.JPG"), bucket=Bucket.SUSPICIOUS, reason="mtime differs")
    report = VerifyReport(
        scan_name="Video SSD",
        card_root=Path("/Volumes/CARD"),
        verdicts=(present, missing, suspicious),
    )

    assert report.present == (present,)
    assert report.missing == (missing,)
    assert report.suspicious == (suspicious,)


def test_report_is_safe_only_when_nothing_is_unresolved():
    clean = VerifyReport(
        scan_name="s",
        card_root=Path("/Volumes/CARD"),
        verdicts=(Verdict(entry=entry(), bucket=Bucket.PRESENT),),
        skipped_user=("DCIM/100NZ_8/DSC_0001.NEF",),
        skipped_junk=(".DS_Store",),
    )
    assert clean.safe_to_format is True
    assert clean.exit_code == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"verdicts": (Verdict(entry=entry(), bucket=Bucket.MISSING),)},
        {"verdicts": (Verdict(entry=entry(), bucket=Bucket.SUSPICIOUS),)},
        {
            "verdicts": (),
            "errors": (WalkError(path=Path("/Volumes/CARD/x"), message="permission denied"),),
        },
    ],
)
def test_report_is_not_safe_when_anything_is_unresolved(kwargs):
    report = VerifyReport(scan_name="s", card_root=Path("/Volumes/CARD"), **kwargs)
    assert report.safe_to_format is False
    assert report.exit_code == 1


def test_counts_covers_every_bucket_exactly_once():
    report = VerifyReport(
        scan_name="s",
        card_root=Path("/Volumes/CARD"),
        verdicts=(
            Verdict(entry=entry("A.JPG"), bucket=Bucket.PRESENT),
            Verdict(entry=entry("B.JPG"), bucket=Bucket.MISSING),
        ),
        skipped_user=("C.NEF",),
        skipped_junk=(".DS_Store", "MISC/AUTPRINT.MRK"),
        errors=(WalkError(path=Path("/Volumes/CARD/bad"), message="io error"),),
    )
    counts = report.counts()

    assert set(counts) == set(Bucket)
    assert counts[Bucket.PRESENT] == 1
    assert counts[Bucket.MISSING] == 1
    assert counts[Bucket.SKIPPED_USER] == 1
    assert counts[Bucket.SKIPPED_JUNK] == 2
    assert counts[Bucket.ERROR] == 1
    assert sum(counts.values()) == report.total_files


def test_scan_holds_multiple_roots_and_a_skip_list():
    scan = Scan(
        name="Video SSD",
        roots=(Path("/Volumes/SSD/Video"), Path("/Volumes/SSD/Photo")),
        scanned_at=datetime(2026, 9, 14, 10, 0, tzinfo=UTC),
        use_mtime=True,
        skip_extensions=frozenset({".nef"}),
        file_count=48213,
    )
    assert len(scan.roots) == 2
    assert ".nef" in scan.skip_extensions


def test_indexed_file_carries_its_destination_path():
    f = IndexedFile(
        path="/Volumes/SSD/Video/2026/IMG_0001.JPG",
        name="IMG_0001.JPG",
        size=100,
        mtime=1000.0,
    )
    assert f.name == "IMG_0001.JPG"
