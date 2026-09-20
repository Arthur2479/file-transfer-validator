from pathlib import Path

import pytest

from ftv.core.deephash import DeepScope, hash_file, resolve
from ftv.core.models import Bucket, FileEntry, IndexedFile, Verdict


def test_hash_file_is_stable_and_content_dependent(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    c = tmp_path / "c.bin"
    a.write_bytes(b"hello world")
    b.write_bytes(b"hello world")
    c.write_bytes(b"hello worlD")

    assert hash_file(a) == hash_file(b)
    assert hash_file(a) != hash_file(c)


def test_hash_file_streams_rather_than_loading_whole_files(tmp_path: Path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 100_000)
    assert hash_file(big, chunk_size=1024) == hash_file(big, chunk_size=65536)


def pair(tmp_path: Path, name: str, card_bytes: bytes, ssd_bytes: bytes) -> Verdict:
    card_dir = tmp_path / "card"
    ssd_dir = tmp_path / "ssd"
    card_dir.mkdir(exist_ok=True)
    ssd_dir.mkdir(exist_ok=True)
    card_path = card_dir / name
    ssd_path = ssd_dir / name
    card_path.write_bytes(card_bytes)
    ssd_path.write_bytes(ssd_bytes)
    return Verdict(
        entry=FileEntry(
            path=card_path, relpath=f"DCIM/{name}", name=name,
            size=len(card_bytes), mtime=1000.0,
        ),
        bucket=Bucket.SUSPICIOUS,
        matched=IndexedFile(path=str(ssd_path), name=name, size=len(ssd_bytes), mtime=2000.0),
        reason="same name and size but a different capture time",
    )


def test_identical_content_resolves_a_suspicious_file_to_present(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"same", b"same")
    result = resolve((verdict,), DeepScope.SUSPICIOUS)

    assert result[0].bucket is Bucket.PRESENT
    assert "content" in result[0].reason.lower()


def test_different_content_resolves_a_suspicious_file_to_missing(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"aaaa", b"bbbb")
    result = resolve((verdict,), DeepScope.SUSPICIOUS)

    assert result[0].bucket is Bucket.MISSING
    assert result[0].matched is None
    assert "content" in result[0].reason.lower()


def test_scope_none_changes_nothing(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"same", b"same")
    assert resolve((verdict,), DeepScope.NONE) == (verdict,)


def test_scope_suspicious_leaves_present_files_alone(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"aaaa", b"bbbb")
    already_present = Verdict(
        entry=verdict.entry, bucket=Bucket.PRESENT, matched=verdict.matched
    )
    result = resolve((already_present,), DeepScope.SUSPICIOUS)
    assert result[0].bucket is Bucket.PRESENT


def test_scope_all_downgrades_a_present_file_whose_content_differs(tmp_path: Path):
    """The exact-size collision that name+size cannot see."""
    verdict = pair(tmp_path, "A.JPG", b"aaaa", b"bbbb")
    claimed_present = Verdict(
        entry=verdict.entry, bucket=Bucket.PRESENT, matched=verdict.matched
    )
    result = resolve((claimed_present,), DeepScope.ALL)

    assert result[0].bucket is Bucket.MISSING
    assert result[0].matched is None


def test_scope_all_keeps_a_genuinely_identical_present_file(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"same", b"same")
    claimed_present = Verdict(
        entry=verdict.entry, bucket=Bucket.PRESENT, matched=verdict.matched
    )
    assert resolve((claimed_present,), DeepScope.ALL)[0].bucket is Bucket.PRESENT


def test_missing_files_are_never_hashed(tmp_path: Path):
    calls: list[Path] = []

    def hasher(path: Path, **_: object) -> str:
        calls.append(path)
        return "x"

    missing = Verdict(
        entry=FileEntry(path=tmp_path / "gone.JPG", relpath="gone.JPG", name="gone.JPG",
                        size=1, mtime=1.0),
        bucket=Bucket.MISSING,
    )
    result = resolve((missing,), DeepScope.ALL, hasher=hasher)

    assert result == (missing,)
    assert calls == [], "a missing file has nothing to compare against"


def test_an_unreadable_file_stays_suspicious_rather_than_passing(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"same", b"same")

    def hasher(path: Path, **_: object) -> str:
        raise PermissionError(13, "Permission denied")

    result = resolve((verdict,), DeepScope.SUSPICIOUS, hasher=hasher)
    assert result[0].bucket is Bucket.SUSPICIOUS
    assert "could not" in result[0].reason.lower() or "denied" in result[0].reason.lower()


def test_deep_scope_parses_the_cli_spellings():
    assert DeepScope.from_flag(None) is DeepScope.NONE
    assert DeepScope.from_flag("") is DeepScope.SUSPICIOUS
    assert DeepScope.from_flag("suspicious") is DeepScope.SUSPICIOUS
    assert DeepScope.from_flag("all") is DeepScope.ALL
    assert DeepScope.from_flag("ALL") is DeepScope.ALL
    with pytest.raises(ValueError, match="deep"):
        DeepScope.from_flag("sometimes")
