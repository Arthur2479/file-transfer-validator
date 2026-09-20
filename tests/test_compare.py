from pathlib import Path

from ftv.core.compare import MTIME_TOLERANCE_SECONDS, compare, timestamps_look_unpreserved
from ftv.core.models import Bucket, FileEntry, IndexedFile, Verdict


class FakeLookup:
    """A Lookup backed by a list, so compare() can be tested with no disk."""

    def __init__(self, files: list[IndexedFile] | None = None) -> None:
        self.files = files or []
        self.calls: list[tuple[str, int]] = []

    def find(self, name: str, size: int) -> list[IndexedFile]:
        self.calls.append((name, size))
        return [f for f in self.files if f.name == name and f.size == size]


def card_file(name: str = "DSC_0001.NEF", size: int = 14, mtime: float = 1000.0) -> FileEntry:
    return FileEntry(
        path=Path("/Volumes/CARD/DCIM/100NZ_8") / name,
        relpath=f"DCIM/100NZ_8/{name}",
        name=name,
        size=size,
        mtime=mtime,
    )


def ssd_file(name: str = "DSC_0001.NEF", size: int = 14, mtime: float = 1000.0,
             folder: str = "2026") -> IndexedFile:
    return IndexedFile(path=f"/Volumes/SSD/{folder}/{name}", name=name, size=size, mtime=mtime)


def test_a_file_with_no_candidate_is_missing():
    verdicts = compare([card_file()], FakeLookup([]))
    assert [v.bucket for v in verdicts] == [Bucket.MISSING]
    assert verdicts[0].matched is None


def test_a_matching_name_size_and_mtime_is_present():
    verdicts = compare([card_file()], FakeLookup([ssd_file()]))
    assert verdicts[0].bucket is Bucket.PRESENT
    assert verdicts[0].matched is not None
    assert verdicts[0].matched.path == "/Volumes/SSD/2026/DSC_0001.NEF"


def test_matching_ignores_where_the_file_sits_on_the_destination():
    """Reorganising the destination must never invalidate a scan."""
    reorganised = ssd_file(folder="2026/september/wedding-shoot")
    verdicts = compare([card_file()], FakeLookup([reorganised]))
    assert verdicts[0].bucket is Bucket.PRESENT


def test_a_different_size_does_not_match():
    verdicts = compare([card_file(size=14)], FakeLookup([ssd_file(size=15)]))
    assert verdicts[0].bucket is Bucket.MISSING


def test_a_truncated_copy_is_reported_missing():
    """A half-copied file has the right name and the wrong size."""
    verdicts = compare([card_file(size=1_000_000)], FakeLookup([ssd_file(size=4096)]))
    assert verdicts[0].bucket is Bucket.MISSING


def test_same_name_same_size_but_a_different_capture_time_is_suspicious():
    """Two cards from one camera both hold DSC_0001.NEF. Different photos."""
    verdicts = compare(
        [card_file(mtime=1_700_000_000.0)],
        FakeLookup([ssd_file(mtime=1_600_000_000.0)]),
    )
    assert verdicts[0].bucket is Bucket.SUSPICIOUS
    assert "capture time" in verdicts[0].reason or "mtime" in verdicts[0].reason
    assert verdicts[0].matched is not None, "the report needs to show what it collided with"


def test_mtime_within_the_tolerance_is_still_present():
    """FAT/exFAT stores mtime at two-second granularity."""
    for delta in (0.0, 1.0, MTIME_TOLERANCE_SECONDS):
        verdicts = compare([card_file(mtime=1000.0)], FakeLookup([ssd_file(mtime=1000.0 + delta)]))
        assert verdicts[0].bucket is Bucket.PRESENT, f"delta {delta} should be tolerated"


def test_mtime_beyond_the_tolerance_is_suspicious():
    verdicts = compare(
        [card_file(mtime=1000.0)],
        FakeLookup([ssd_file(mtime=1000.0 + MTIME_TOLERANCE_SECONDS + 0.5)]),
    )
    assert verdicts[0].bucket is Bucket.SUSPICIOUS


def test_the_tolerance_is_symmetric():
    verdicts = compare([card_file(mtime=1000.0)], FakeLookup([ssd_file(mtime=999.0)]))
    assert verdicts[0].bucket is Bucket.PRESENT


def test_disabling_the_mtime_signal_accepts_any_timestamp():
    verdicts = compare(
        [card_file(mtime=1_700_000_000.0)],
        FakeLookup([ssd_file(mtime=1.0)]),
        use_mtime=False,
    )
    assert verdicts[0].bucket is Bucket.PRESENT


def test_disabling_mtime_is_present_regardless_of_which_candidate_matches():
    """With the mtime signal off, any name+size candidate should pass.

    Which candidate ends up in ``matched`` isn't a guaranteed contract here
    (it's whatever the Lookup returns first) -- only the bucket is asserted.
    """
    april = ssd_file(mtime=1_600_000_000.0, folder="april")
    september = ssd_file(mtime=1_700_000_000.0, folder="september")
    verdicts = compare(
        [card_file(mtime=1_700_000_000.0)],
        FakeLookup([april, september]),
        use_mtime=False,
    )
    assert verdicts[0].bucket is Bucket.PRESENT


def test_the_right_candidate_is_chosen_from_several_same_name_same_size_files():
    """April's DSC_0001.NEF and September's are both indexed; pick by capture time."""
    april = ssd_file(mtime=1_600_000_000.0, folder="april")
    september = ssd_file(mtime=1_700_000_000.0, folder="september")
    verdicts = compare([card_file(mtime=1_700_000_000.0)], FakeLookup([april, september]))

    assert verdicts[0].bucket is Bucket.PRESENT
    assert verdicts[0].matched is not None
    assert "september" in verdicts[0].matched.path


def test_the_closest_candidate_is_chosen_when_two_are_both_within_tolerance():
    """Both candidates qualify (diffs 1.0s and 0.5s); the closer one must win.

    A looser implementation that returns the first qualifying candidate,
    rather than the closest one, would pass every other test in this file
    but fail this one.
    """
    farther = ssd_file(mtime=500.0, folder="farther")
    closer = ssd_file(mtime=501.5, folder="closer")
    verdicts = compare([card_file(mtime=501.0)], FakeLookup([farther, closer]))

    assert verdicts[0].bucket is Bucket.PRESENT
    assert verdicts[0].matched is not None
    assert "closer" in verdicts[0].matched.path


def test_a_collision_with_no_matching_capture_time_is_suspicious_not_present():
    april = ssd_file(mtime=1_600_000_000.0, folder="april")
    june = ssd_file(mtime=1_650_000_000.0, folder="june")
    verdicts = compare([card_file(mtime=1_700_000_000.0)], FakeLookup([april, june]))
    assert verdicts[0].bucket is Bucket.SUSPICIOUS


def test_verdicts_are_returned_in_the_order_the_entries_arrived():
    entries = [card_file(name=f"IMG_{n}.JPG", size=n) for n in (3, 1, 2)]
    verdicts = compare(entries, FakeLookup([]))
    assert [v.entry.name for v in verdicts] == ["IMG_3.JPG", "IMG_1.JPG", "IMG_2.JPG"]


def test_every_entry_produces_exactly_one_verdict():
    entries = [card_file(name=f"IMG_{n}.JPG", size=n) for n in range(50)]
    verdicts = compare(entries, FakeLookup([ssd_file(name="IMG_7.JPG", size=7)]))
    assert len(verdicts) == 50
    assert sum(1 for v in verdicts if v.bucket is Bucket.PRESENT) == 1


def test_comparing_nothing_yields_nothing():
    assert compare([], FakeLookup([])) == ()


def test_compare_queries_the_lookup_once_per_entry():
    lookup = FakeLookup([])
    compare([card_file(name="A.JPG", size=1), card_file(name="B.JPG", size=2)], lookup)
    assert lookup.calls == [("A.JPG", 1), ("B.JPG", 2)]


def test_compare_accepts_a_generator():
    verdicts = compare((card_file(name=f"{n}.JPG", size=n) for n in range(3)), FakeLookup([]))
    assert len(verdicts) == 3


def test_unpreserved_timestamps_are_detected_when_every_match_is_suspicious():
    """The signature of a copy tool that drops timestamps."""
    verdicts = tuple(
        Verdict(entry=card_file(name=f"IMG_{n}.JPG"), bucket=Bucket.SUSPICIOUS,
                matched=ssd_file(name=f"IMG_{n}.JPG"), reason="capture time differs")
        for n in range(20)
    )
    assert timestamps_look_unpreserved(verdicts) is True


def test_unpreserved_timestamps_are_not_claimed_when_some_files_matched_cleanly():
    verdicts = (
        Verdict(entry=card_file(name="A.JPG"), bucket=Bucket.PRESENT, matched=ssd_file()),
        *(
            Verdict(entry=card_file(name=f"B{n}.JPG"), bucket=Bucket.SUSPICIOUS,
                    matched=ssd_file(), reason="capture time differs")
            for n in range(20)
        ),
    )
    assert timestamps_look_unpreserved(verdicts) is False


def test_unpreserved_timestamps_are_not_claimed_on_a_tiny_sample():
    """Two colliding files are a real collision, not a broken copy tool."""
    verdicts = tuple(
        Verdict(entry=card_file(name=f"IMG_{n}.JPG"), bucket=Bucket.SUSPICIOUS,
                matched=ssd_file(), reason="capture time differs")
        for n in range(2)
    )
    assert timestamps_look_unpreserved(verdicts) is False


def test_unpreserved_timestamps_ignores_missing_files():
    """Missing files have no match, so they say nothing about timestamps."""
    verdicts = tuple(
        Verdict(entry=card_file(name=f"IMG_{n}.JPG"), bucket=Bucket.MISSING) for n in range(50)
    )
    assert timestamps_look_unpreserved(verdicts) is False
