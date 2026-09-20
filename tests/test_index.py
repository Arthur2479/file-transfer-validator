from datetime import datetime
from pathlib import Path

import pytest

from ftv.core.index import Index, ScanExistsError, ScanNotFoundError
from ftv.core.models import FileEntry, Lookup


def entry(name: str, size: int, mtime: float = 1000.0, folder: str = "2026") -> FileEntry:
    return FileEntry(
        path=Path(f"/Volumes/SSD/{folder}/{name}"),
        relpath=f"{folder}/{name}",
        name=name,
        size=size,
        mtime=mtime,
    )


@pytest.fixture
def index(tmp_path: Path) -> Index:
    idx = Index.open(tmp_path / "index.db")
    yield idx
    idx.close()


def test_saving_a_scan_returns_it_with_a_file_count(index: Index):
    scan = index.save_scan(
        "Video SSD",
        roots=[Path("/Volumes/SSD/Video"), Path("/Volumes/SSD/Photo")],
        entries=[entry("A.JPG", 10), entry("B.MP4", 20)],
        use_mtime=True,
        skip_extensions=frozenset({".nef"}),
    )

    assert scan.name == "Video SSD"
    assert scan.file_count == 2
    assert len(scan.roots) == 2
    assert scan.skip_extensions == frozenset({".nef"})
    assert isinstance(scan.scanned_at, datetime)


def test_a_saved_scan_round_trips(index: Index):
    index.save_scan(
        "Video SSD",
        roots=[Path("/Volumes/SSD/Video")],
        entries=[entry("A.JPG", 10)],
        use_mtime=False,
        skip_extensions=frozenset({".nef", ".cr3"}),
    )

    loaded = index.get_scan("Video SSD")
    assert loaded is not None
    assert loaded.use_mtime is False
    assert loaded.skip_extensions == frozenset({".nef", ".cr3"})
    assert loaded.roots == (Path("/Volumes/SSD/Video"),)
    assert loaded.file_count == 1


def test_get_scan_returns_none_for_an_unknown_name(index: Index):
    assert index.get_scan("nope") is None


def test_saving_a_duplicate_name_is_refused_unless_replacing(index: Index):
    index.save_scan("S", roots=[Path("/a")], entries=[entry("A.JPG", 10)],
                    use_mtime=True, skip_extensions=frozenset())

    with pytest.raises(ScanExistsError, match="S"):
        index.save_scan("S", roots=[Path("/a")], entries=[entry("B.JPG", 11)],
                        use_mtime=True, skip_extensions=frozenset())

    replaced = index.save_scan("S", roots=[Path("/a")], entries=[entry("B.JPG", 11)],
                               use_mtime=True, skip_extensions=frozenset(), replace=True)
    assert replaced.file_count == 1
    assert index.lookup_for("S").find("A.JPG", 10) == []
    assert len(index.lookup_for("S").find("B.JPG", 11)) == 1


def test_list_scans_is_sorted_by_name(index: Index):
    for name in ("Zulu", "Alpha", "Mike"):
        index.save_scan(name, roots=[Path("/a")], entries=[], use_mtime=True,
                        skip_extensions=frozenset())
    assert [s.name for s in index.list_scans()] == ["Alpha", "Mike", "Zulu"]


def test_deleting_a_scan_removes_its_files(index: Index):
    index.save_scan("S", roots=[Path("/a")], entries=[entry("A.JPG", 10)],
                    use_mtime=True, skip_extensions=frozenset())

    assert index.delete_scan("S") is True
    assert index.get_scan("S") is None
    assert index.delete_scan("S") is False


def test_lookup_matches_on_name_and_size_ignoring_path(index: Index):
    index.save_scan(
        "S",
        roots=[Path("/a")],
        entries=[entry("IMG_0001.JPG", 42, folder="2026/shoot-b")],
        use_mtime=True,
        skip_extensions=frozenset(),
    )
    lookup = index.lookup_for("S")

    assert isinstance(lookup, Lookup)
    hits = lookup.find("IMG_0001.JPG", 42)
    assert len(hits) == 1
    assert hits[0].path == "/Volumes/SSD/2026/shoot-b/IMG_0001.JPG"
    assert hits[0].mtime == 1000.0


def test_lookup_does_not_match_a_different_size(index: Index):
    index.save_scan("S", roots=[Path("/a")], entries=[entry("A.JPG", 10)],
                    use_mtime=True, skip_extensions=frozenset())
    assert index.lookup_for("S").find("A.JPG", 11) == []


def test_lookup_returns_every_candidate_with_the_same_name_and_size(index: Index):
    index.save_scan(
        "S",
        roots=[Path("/a")],
        entries=[
            entry("DSC_0001.NEF", 14, mtime=100.0, folder="april"),
            entry("DSC_0001.NEF", 14, mtime=900.0, folder="september"),
        ],
        use_mtime=True,
        skip_extensions=frozenset(),
    )
    hits = index.lookup_for("S").find("DSC_0001.NEF", 14)

    assert len(hits) == 2, "the mtime signal needs every candidate to choose from"
    assert {h.mtime for h in hits} == {100.0, 900.0}


def test_lookup_is_scoped_to_its_scan(index: Index):
    index.save_scan("A", roots=[Path("/a")], entries=[entry("X.JPG", 1)],
                    use_mtime=True, skip_extensions=frozenset())
    index.save_scan("B", roots=[Path("/b")], entries=[entry("Y.JPG", 2)],
                    use_mtime=True, skip_extensions=frozenset())

    assert index.lookup_for("A").find("Y.JPG", 2) == []
    assert index.lookup_for("B").find("Y.JPG", 2) != []


def test_lookup_for_an_unknown_scan_raises(index: Index):
    with pytest.raises(ScanNotFoundError, match="nope"):
        index.lookup_for("nope")


def test_opening_creates_the_parent_directory(tmp_path: Path):
    db = tmp_path / "nested" / "deeper" / "index.db"
    idx = Index.open(db)
    idx.close()
    assert db.exists()


def test_opening_refuses_a_db_inside_a_scan_root(tmp_path: Path):
    from ftv.paths import DbPathError

    root = tmp_path / "ssd"
    root.mkdir()
    with pytest.raises(DbPathError):
        Index.open(root / "index.db", roots=[root])


def test_reopening_an_existing_db_preserves_scans(tmp_path: Path):
    db = tmp_path / "index.db"
    first = Index.open(db)
    first.save_scan("S", roots=[Path("/a")], entries=[entry("A.JPG", 10)],
                    use_mtime=True, skip_extensions=frozenset())
    first.close()

    second = Index.open(db)
    try:
        assert second.get_scan("S") is not None
        assert len(second.lookup_for("S").find("A.JPG", 10)) == 1
    finally:
        second.close()


def test_migration_is_idempotent(tmp_path: Path):
    from ftv.core.index import SCHEMA_VERSION

    db = tmp_path / "index.db"
    for _ in range(3):
        idx = Index.open(db)
        assert idx.schema_version() == SCHEMA_VERSION
        idx.close()


def test_a_large_scan_saves_and_looks_up(index: Index):
    entries = [entry(f"IMG_{n:05}.JPG", n) for n in range(5000)]
    scan = index.save_scan("Big", roots=[Path("/a")], entries=entries,
                           use_mtime=True, skip_extensions=frozenset())
    assert scan.file_count == 5000

    lookup = index.lookup_for("Big")
    assert len(lookup.find("IMG_04999.JPG", 4999)) == 1
    assert lookup.find("IMG_04999.JPG", 1) == []
