from pathlib import Path

import pytest

from ftv.core import fsread
from ftv.core.models import WalkError


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "DCIM" / "100NZ_8").mkdir(parents=True)
    (tmp_path / "DCIM" / "100NZ_8" / "DSC_0001.NEF").write_bytes(b"a" * 10)
    (tmp_path / "DCIM" / "100NZ_8" / "DSC_0002.JPG").write_bytes(b"b" * 20)
    (tmp_path / "MISC").mkdir()
    (tmp_path / "MISC" / "AUTPRINT.MRK").write_bytes(b"junk")
    (tmp_path / "root.txt").write_bytes(b"c")
    return tmp_path


def test_stat_file_returns_size_and_mtime(tree: Path):
    size, mtime = fsread.stat_file(tree / "DCIM" / "100NZ_8" / "DSC_0001.NEF")
    assert size == 10
    assert mtime > 0


def test_walk_yields_every_file_with_posix_relpath(tree: Path):
    found = [f for f in fsread.walk(tree) if not isinstance(f, WalkError)]
    assert sorted(f.relpath for f in found) == [
        "DCIM/100NZ_8/DSC_0001.NEF",
        "DCIM/100NZ_8/DSC_0002.JPG",
        "MISC/AUTPRINT.MRK",
        "root.txt",
    ]


def test_walk_returns_absolute_paths(tree: Path):
    found = [f for f in fsread.walk(tree) if not isinstance(f, WalkError)]
    assert all(f.path.is_absolute() for f in found)
    assert all(f.path.exists() for f in found)


def test_walk_prunes_directories_the_caller_rejects(tree: Path):
    found = [
        f for f in fsread.walk(tree, prune=lambda name: name == "MISC")
        if not isinstance(f, WalkError)
    ]
    assert not any(f.relpath.startswith("MISC/") for f in found)
    assert len(found) == 3


def test_walk_skips_symlinks_rather_than_following_them(tree: Path):
    (tree / "link").symlink_to(tree / "DCIM")
    found = [f for f in fsread.walk(tree) if not isinstance(f, WalkError)]
    assert not any(f.relpath.startswith("link") for f in found)


def test_walk_reports_a_missing_root_as_an_error(tmp_path: Path):
    results = list(fsread.walk(tmp_path / "nope"))
    assert len(results) == 1
    assert isinstance(results[0], WalkError)
    assert "nope" in str(results[0].path)


def test_walk_reports_an_unreadable_directory_without_aborting(tree: Path):
    locked = tree / "locked"
    locked.mkdir()
    (locked / "hidden.JPG").write_bytes(b"x")
    locked.chmod(0o000)
    try:
        results = list(fsread.walk(tree))
    finally:
        locked.chmod(0o755)

    files = [r for r in results if not isinstance(r, WalkError)]
    errors = [r for r in results if isinstance(r, WalkError)]
    assert len(files) == 4, "files outside the locked directory are still reported"
    assert len(errors) == 1


def test_read_chunks_streams_the_whole_file(tree: Path):
    target = tree / "DCIM" / "100NZ_8" / "DSC_0002.JPG"
    chunks = list(fsread.read_chunks(target, chunk_size=7))
    assert b"".join(chunks) == b"b" * 20
    assert len(chunks) == 3
