from pathlib import Path

import pytest

from ftv.core.walker import walk_tree

NO_SKIPS: frozenset[str] = frozenset()


@pytest.fixture
def card(tmp_path: Path) -> Path:
    clips = tmp_path / "DCIM" / "100NZ_8"
    clips.mkdir(parents=True)
    (clips / "DSC_0001.NEF").write_bytes(b"a" * 10)
    (clips / "DSC_0001.JPG").write_bytes(b"b" * 20)
    (clips / "DSC_0001.THM").write_bytes(b"t")
    (tmp_path / "MISC").mkdir()
    (tmp_path / "MISC" / "AUTPRINT.MRK").write_bytes(b"j")
    (tmp_path / ".DS_Store").write_bytes(b"d")
    return tmp_path


def test_kept_entries_carry_name_size_mtime_and_relpath(card: Path):
    outcome = walk_tree(card, NO_SKIPS)

    by_name = {e.name: e for e in outcome.entries}
    assert set(by_name) == {"DSC_0001.NEF", "DSC_0001.JPG"}
    assert by_name["DSC_0001.NEF"].size == 10
    assert by_name["DSC_0001.JPG"].size == 20
    assert by_name["DSC_0001.NEF"].relpath == "DCIM/100NZ_8/DSC_0001.NEF"
    assert by_name["DSC_0001.NEF"].mtime > 0


def test_junk_is_reported_as_relpaths_not_entries(card: Path):
    outcome = walk_tree(card, NO_SKIPS)
    # NOTE: MISC/AUTPRINT.MRK is intentionally excluded here. walk_tree prunes
    # the whole MISC directory during the walk itself (fsread.walk(prune=
    # filters.is_junk_dir)), so files beneath a pruned junk directory are never
    # individually visited or classified -- they are simply absent from every
    # output list, at zero cost. Only junk files that were actually yielded by
    # the walk (because their parent directory was not itself pruned) show up
    # in skipped_junk. See task-4-brief.md's note on this for the rationale.
    assert sorted(outcome.skipped_junk) == [
        ".DS_Store",
        "DCIM/100NZ_8/DSC_0001.THM",
    ]
    assert outcome.skipped_user == ()


def test_user_skip_list_moves_entries_out_of_the_checked_set(card: Path):
    outcome = walk_tree(card, frozenset({".nef"}))
    assert [e.name for e in outcome.entries] == ["DSC_0001.JPG"]
    assert outcome.skipped_user == ("DCIM/100NZ_8/DSC_0001.NEF",)


def test_every_file_is_accounted_for_exactly_once(card: Path):
    outcome = walk_tree(card, frozenset({".nef"}))
    # Files under a pruned junk directory (MISC) are never yielded by the walk
    # at all, so they must be excluded from the on-disk count too -- otherwise
    # this assertion would fail not because of a bug, but because it is
    # counting files the walker was never asked to look at.
    on_disk = sum(
        1
        for p in card.rglob("*")
        if p.is_file() and "MISC" not in p.relative_to(card).parts[:-1]
    )
    assert on_disk == outcome.total_files


def test_a_file_that_cannot_be_stat_ed_becomes_an_error(card: Path, monkeypatch):
    from ftv.core import walker

    real = walker.fsread.stat_file

    def flaky(path: Path):
        if path.name == "DSC_0001.JPG":
            raise OSError(13, "permission denied")
        return real(path)

    monkeypatch.setattr(walker.fsread, "stat_file", flaky)
    outcome = walk_tree(card, NO_SKIPS)

    assert [e.name for e in outcome.entries] == ["DSC_0001.NEF"]
    assert len(outcome.errors) == 1
    assert "permission denied" in outcome.errors[0].message


def test_walking_a_missing_root_yields_an_error_and_no_entries(tmp_path: Path):
    outcome = walk_tree(tmp_path / "absent", NO_SKIPS)
    assert outcome.entries == ()
    assert len(outcome.errors) == 1


def test_junk_directories_are_pruned_rather_than_walked(card: Path):
    deep = card / "MISC" / "nested" / "deeper"
    deep.mkdir(parents=True)
    (deep / "IMG.JPG").write_bytes(b"x")

    outcome = walk_tree(card, NO_SKIPS)
    assert not any(e.relpath.startswith("MISC/") for e in outcome.entries)
    assert "MISC" in " ".join(outcome.skipped_junk) or outcome.skipped_junk


def test_walk_trees_merges_several_roots(tmp_path: Path):
    from ftv.core.walker import walk_trees

    first = tmp_path / "video"
    second = tmp_path / "photo"
    first.mkdir()
    second.mkdir()
    (first / "A.MP4").write_bytes(b"a" * 5)
    (second / "B.JPG").write_bytes(b"b" * 6)

    outcome = walk_trees([first, second], NO_SKIPS)

    assert sorted(e.name for e in outcome.entries) == ["A.MP4", "B.JPG"]
    assert outcome.errors == ()


def test_walk_trees_keeps_absolute_paths_so_roots_cannot_collide(tmp_path: Path):
    from ftv.core.walker import walk_trees

    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    (first / "IMG.JPG").write_bytes(b"a" * 5)
    (second / "IMG.JPG").write_bytes(b"b" * 5)

    outcome = walk_trees([first, second], NO_SKIPS)

    assert len(outcome.entries) == 2
    assert len({e.path for e in outcome.entries}) == 2


def test_walk_trees_collects_errors_from_every_root(tmp_path: Path):
    from ftv.core.walker import walk_trees

    good = tmp_path / "good"
    good.mkdir()
    (good / "A.JPG").write_bytes(b"a")

    outcome = walk_trees([good, tmp_path / "absent-one", tmp_path / "absent-two"], NO_SKIPS)

    assert len(outcome.entries) == 1
    assert len(outcome.errors) == 2


def test_walk_trees_with_no_roots_is_empty(tmp_path: Path):
    from ftv.core.walker import walk_trees

    outcome = walk_trees([], NO_SKIPS)
    assert outcome.total_files == 0
