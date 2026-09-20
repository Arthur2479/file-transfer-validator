from pathlib import Path

from ftv.core.confirm import confirm
from ftv.core.models import Bucket, FileEntry, IndexedFile, Verdict


def card_file(name: str = "A.JPG", size: int = 10) -> FileEntry:
    return FileEntry(
        path=Path("/Volumes/CARD/DCIM") / name,
        relpath=f"DCIM/{name}",
        name=name,
        size=size,
        mtime=1000.0,
    )


def indexed(name: str = "A.JPG", size: int = 10) -> IndexedFile:
    return IndexedFile(path=f"/Volumes/SSD/{name}", name=name, size=size, mtime=1000.0)


def present(name: str = "A.JPG", size: int = 10) -> Verdict:
    return Verdict(entry=card_file(name, size), bucket=Bucket.PRESENT, matched=indexed(name, size))


def test_a_file_still_on_the_destination_stays_present():
    def stat_file(path: Path) -> tuple[int, float]:
        return 10, 1000.0

    result = confirm((present(),), stat_file=stat_file)
    assert result[0].bucket is Bucket.PRESENT


def test_a_file_deleted_after_the_scan_becomes_suspicious():
    """The dangerous staleness direction: the index lies, the disk does not."""

    def stat_file(path: Path) -> tuple[int, float]:
        raise FileNotFoundError(2, "No such file or directory")

    result = confirm((present(),), stat_file=stat_file)
    assert result[0].bucket is Bucket.SUSPICIOUS
    assert "no longer" in result[0].reason.lower() or "not found" in result[0].reason.lower()
    assert result[0].matched is not None, "the report still needs to name the path it checked"


def test_a_file_whose_size_changed_after_the_scan_becomes_suspicious():
    def stat_file(path: Path) -> tuple[int, float]:
        return 4096, 1000.0

    result = confirm((present(),), stat_file=stat_file)
    assert result[0].bucket is Bucket.SUSPICIOUS
    assert "size" in result[0].reason.lower()


def test_an_unreadable_destination_file_becomes_suspicious():
    def stat_file(path: Path) -> tuple[int, float]:
        raise PermissionError(13, "Permission denied")

    result = confirm((present(),), stat_file=stat_file)
    assert result[0].bucket is Bucket.SUSPICIOUS


def test_missing_verdicts_are_passed_through_untouched():
    calls: list[Path] = []

    def stat_file(path: Path) -> tuple[int, float]:
        calls.append(path)
        return 10, 1000.0

    missing = Verdict(entry=card_file(), bucket=Bucket.MISSING)
    result = confirm((missing,), stat_file=stat_file)

    assert result == (missing,)
    assert calls == [], "a missing file has no destination path to confirm"


def test_already_suspicious_verdicts_are_passed_through_untouched():
    calls: list[Path] = []

    def stat_file(path: Path) -> tuple[int, float]:
        calls.append(path)
        return 10, 1000.0

    suspicious = Verdict(
        entry=card_file(), bucket=Bucket.SUSPICIOUS, matched=indexed(), reason="capture time"
    )
    result = confirm((suspicious,), stat_file=stat_file)

    assert result[0].reason == "capture time", "the original reason must survive"
    assert calls == []


def test_confirm_stats_the_matched_destination_path_not_the_card_path():
    seen: list[Path] = []

    def stat_file(path: Path) -> tuple[int, float]:
        seen.append(path)
        return 10, 1000.0

    confirm((present(),), stat_file=stat_file)
    assert seen == [Path("/Volumes/SSD/A.JPG")]


def test_confirm_checks_each_present_file_exactly_once():
    seen: list[Path] = []

    def stat_file(path: Path) -> tuple[int, float]:
        seen.append(path)
        return 10, 1000.0

    verdicts = tuple(present(name=f"IMG_{n}.JPG") for n in range(25))
    confirm(verdicts, stat_file=stat_file)
    assert len(seen) == 25


def test_confirm_against_the_real_filesystem(tmp_path: Path):
    real = tmp_path / "IMG.JPG"
    real.write_bytes(b"x" * 10)
    verdict = Verdict(
        entry=card_file("IMG.JPG", 10),
        bucket=Bucket.PRESENT,
        matched=IndexedFile(path=str(real), name="IMG.JPG", size=10, mtime=1000.0),
    )

    assert confirm((verdict,))[0].bucket is Bucket.PRESENT

    real.unlink()
    assert confirm((verdict,))[0].bucket is Bucket.SUSPICIOUS
