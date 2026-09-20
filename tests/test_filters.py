import pytest

from ftv.core.filters import Decision, decide, is_junk_dir, normalize_extension, normalize_skip_list

NO_SKIPS: frozenset[str] = frozenset()


@pytest.mark.parametrize(
    "relpath",
    [
        "DCIM/100NZ_8/DSC_0001.NEF",
        "DCIM/100NZ_8/DSC_0002.JPG",
        "PRIVATE/M4ROOT/CLIP/C0007.MP4",
        "notes.txt",
        "DCIM/weird_no_extension",
        "DCIM/100NZ_8/DSC_0003.SomeFutureFormat",
    ],
)
def test_unrecognised_files_are_kept(relpath):
    """An allowlist would make a new camera format invisible. Keep by default."""
    assert decide(relpath, NO_SKIPS) is Decision.KEEP


@pytest.mark.parametrize(
    "relpath",
    [
        "DCIM/100NZ_8/DSC_0001.THM",
        "DCIM/100NZ_8/DSC_0001.thm",
        "MISC/AUTPRINT.MRK",
        "misc/anything.dat",
        ".DS_Store",
        "DCIM/.DS_Store",
        "System Volume Information/WPSettings.dat",
        ".fseventsd/0000000000000001",
        ".Spotlight-V100/store.db",
        ".Trashes/501/old.jpg",
        "DCIM/._DSC_0001.JPG",
        "NIKON001.CTG",
    ],
)
def test_camera_and_os_junk_is_skipped_as_junk(relpath):
    assert decide(relpath, NO_SKIPS) is Decision.SKIP_JUNK


def test_user_skip_list_wins_over_keep():
    assert decide("DCIM/100NZ_8/DSC_0001.NEF", frozenset({".nef"})) is Decision.SKIP_USER


def test_user_skip_list_is_case_insensitive():
    assert decide("DCIM/100NZ_8/dsc_0001.nef", frozenset({".nef"})) is Decision.SKIP_USER
    assert decide("DCIM/100NZ_8/DSC_0001.NEF", frozenset({".nef"})) is Decision.SKIP_USER


def test_junk_wins_over_user_skip_list():
    """Reporting junk as a deliberate user skip would overstate what was skipped."""
    assert decide("DCIM/DSC_0001.THM", frozenset({".thm"})) is Decision.SKIP_JUNK


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (".NEF", ".nef"),
        ("NEF", ".nef"),
        ("nef", ".nef"),
        ("*.nef", ".nef"),
        ("  .NEF  ", ".nef"),
    ],
)
def test_normalize_extension_accepts_the_forms_people_type(raw, expected):
    assert normalize_extension(raw) == expected


def test_normalize_skip_list_splits_and_dedupes():
    assert normalize_skip_list(["NEF", "*.nef", ".CR3"]) == frozenset({".nef", ".cr3"})
    assert normalize_skip_list([".NEF,.CR3"]) == frozenset({".nef", ".cr3"})
    assert normalize_skip_list([]) == frozenset()
    assert normalize_skip_list(None) == frozenset()


def test_normalize_extension_rejects_an_empty_value():
    with pytest.raises(ValueError, match="empty"):
        normalize_extension("  ")


@pytest.mark.parametrize(
    "name", ["MISC", "misc", ".fseventsd", "System Volume Information", ".Trashes"]
)
def test_junk_directories_are_recognised_for_pruning(name):
    assert is_junk_dir(name) is True


@pytest.mark.parametrize("name", ["DCIM", "100NZ_8", "PRIVATE", "Video"])
def test_real_directories_are_not_pruned(name):
    assert is_junk_dir(name) is False
