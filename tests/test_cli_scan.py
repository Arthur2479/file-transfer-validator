import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ftv.cli import app
from ftv.core.index import Index

runner = CliRunner()


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    # Rich's Console falls back to 80 columns whenever it can't read a real
    # terminal size (always, under CliRunner), and pytest's tmp_path can be
    # long enough (deeply nested under /private/var/.../pytest-of-.../) that a
    # full destination path silently truncates at 80 columns in table/text
    # output. Rich checks the COLUMNS env var before that fallback, so setting
    # it here keeps assertions on rendered paths deterministic without
    # changing cli.py's Console construction (which real terminal sessions
    # rely on auto-sizing correctly).
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "index.db"


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    root = tmp_path / "ssd" / "Video"
    root.mkdir(parents=True)
    (root / "A.MP4").write_bytes(b"a" * 10)
    (root / "B.JPG").write_bytes(b"b" * 20)
    (root / "C.NEF").write_bytes(b"c" * 30)
    (root / ".DS_Store").write_bytes(b"junk")
    return root


def invoke(*args: str):
    return runner.invoke(app, list(args))


def test_scan_add_stores_a_named_scan(db: Path, destination: Path):
    result = invoke("scan", "add", "Video SSD", str(destination), "--db", str(db))

    assert result.exit_code == 0, result.output
    assert "Video SSD" in result.output
    assert "3" in result.output

    index = Index.open(db)
    try:
        scan = index.get_scan("Video SSD")
        assert scan is not None
        assert scan.file_count == 3
        assert scan.roots == (destination.resolve(),)
    finally:
        index.close()


def test_scan_add_accepts_several_roots(db: Path, destination: Path, tmp_path: Path):
    other = tmp_path / "ssd" / "Photo"
    other.mkdir(parents=True)
    (other / "D.JPG").write_bytes(b"d" * 5)

    result = invoke("scan", "add", "Both", str(destination), str(other), "--db", str(db))
    assert result.exit_code == 0, result.output

    index = Index.open(db)
    try:
        scan = index.get_scan("Both")
        assert scan is not None
        assert scan.file_count == 4
        assert len(scan.roots) == 2
    finally:
        index.close()


def test_scan_add_records_a_skip_list(db: Path, destination: Path):
    result = invoke("scan", "add", "S", str(destination), "--skip", ".NEF", "--db", str(db))
    assert result.exit_code == 0, result.output

    index = Index.open(db)
    try:
        scan = index.get_scan("S")
        assert scan is not None
        assert scan.skip_extensions == frozenset({".nef"})
        assert scan.file_count == 2, "the skipped RAW is not indexed"
    finally:
        index.close()


def test_scan_add_accepts_a_comma_separated_skip_list(db: Path, destination: Path):
    invoke("scan", "add", "S", str(destination), "--skip", "NEF,jpg", "--db", str(db))

    index = Index.open(db)
    try:
        scan = index.get_scan("S")
        assert scan is not None
        assert scan.skip_extensions == frozenset({".nef", ".jpg"})
    finally:
        index.close()


def test_scan_add_records_the_no_mtime_choice(db: Path, destination: Path):
    invoke("scan", "add", "S", str(destination), "--no-mtime", "--db", str(db))

    index = Index.open(db)
    try:
        scan = index.get_scan("S")
        assert scan is not None
        assert scan.use_mtime is False
    finally:
        index.close()


def test_scan_add_refuses_a_duplicate_name(db: Path, destination: Path):
    invoke("scan", "add", "S", str(destination), "--db", str(db))
    result = invoke("scan", "add", "S", str(destination), "--db", str(db))

    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "refresh" in result.output


def test_scan_add_refuses_a_root_that_does_not_exist(db: Path, tmp_path: Path):
    result = invoke("scan", "add", "S", str(tmp_path / "nope"), "--db", str(db))

    assert result.exit_code != 0
    assert "nope" in result.output


def test_scan_add_reports_unreadable_files_without_storing_a_silent_scan(
    db: Path, destination: Path
):
    locked = destination / "locked"
    locked.mkdir()
    (locked / "hidden.JPG").write_bytes(b"x")
    locked.chmod(0o000)
    try:
        result = invoke("scan", "add", "S", str(destination), "--db", str(db))
    finally:
        locked.chmod(0o755)

    assert "1" in result.output
    assert "unreadable" in result.output.lower() or "error" in result.output.lower()


def test_scan_list_shows_the_stored_scans(db: Path, destination: Path):
    invoke("scan", "add", "Video SSD", str(destination), "--db", str(db))
    result = invoke("scan", "list", "--db", str(db))

    assert result.exit_code == 0
    assert "Video SSD" in result.output
    assert str(destination) in result.output


def test_scan_list_is_friendly_when_empty(db: Path):
    # scan list refuses to auto-create the index (fix 2), so the db must
    # already exist -- with zero scans in it -- for this to test the
    # friendly-empty-listing behaviour rather than the "no db yet" refusal.
    index = Index.open(db)
    index.close()

    result = invoke("scan", "list", "--db", str(db))
    assert result.exit_code == 0
    assert "no scans" in result.output.lower()


def test_scan_refresh_picks_up_new_files(db: Path, destination: Path):
    invoke("scan", "add", "S", str(destination), "--db", str(db))
    (destination / "D.MP4").write_bytes(b"d" * 40)

    result = invoke("scan", "refresh", "S", "--db", str(db))
    assert result.exit_code == 0, result.output

    index = Index.open(db)
    try:
        scan = index.get_scan("S")
        assert scan is not None
        assert scan.file_count == 4
    finally:
        index.close()


def test_scan_refresh_drops_files_deleted_from_the_destination(db: Path, destination: Path):
    invoke("scan", "add", "S", str(destination), "--db", str(db))
    (destination / "A.MP4").unlink()

    invoke("scan", "refresh", "S", "--db", str(db))

    index = Index.open(db)
    try:
        assert index.lookup_for("S").find("A.MP4", 10) == []
    finally:
        index.close()


def test_scan_refresh_preserves_the_skip_list_and_mtime_setting(db: Path, destination: Path):
    invoke("scan", "add", "S", str(destination), "--skip", ".NEF", "--no-mtime", "--db", str(db))
    invoke("scan", "refresh", "S", "--db", str(db))

    index = Index.open(db)
    try:
        scan = index.get_scan("S")
        assert scan is not None
        assert scan.skip_extensions == frozenset({".nef"})
        assert scan.use_mtime is False
    finally:
        index.close()


def test_scan_refresh_reports_an_unknown_name(db: Path, destination: Path):
    # The db must already exist (scan refresh no longer auto-creates one, per
    # fix 2), so add an unrelated scan first to bring the index into being.
    invoke("scan", "add", "Other", str(destination), "--db", str(db))

    result = invoke("scan", "refresh", "nope", "--db", str(db))
    assert result.exit_code != 0
    assert "nope" in result.output


def test_scan_refresh_refuses_when_a_root_is_not_mounted(
    db: Path, destination: Path, tmp_path: Path
):
    invoke("scan", "add", "S", str(destination), "--db", str(db))
    import shutil

    shutil.rmtree(destination)

    result = invoke("scan", "refresh", "S", "--db", str(db))
    assert result.exit_code != 0
    assert "not available" in result.output.lower() or "unavailable" in result.output.lower()


def test_scan_rm_deletes_a_scan(db: Path, destination: Path):
    invoke("scan", "add", "S", str(destination), "--db", str(db))
    result = invoke("scan", "rm", "S", "--db", str(db))

    assert result.exit_code == 0
    index = Index.open(db)
    try:
        assert index.get_scan("S") is None
    finally:
        index.close()


def test_scan_rm_reports_an_unknown_name(db: Path, destination: Path):
    # The db must already exist (scan rm no longer auto-creates one, per
    # fix 2), so add an unrelated scan first to bring the index into being.
    invoke("scan", "add", "Other", str(destination), "--db", str(db))

    result = invoke("scan", "rm", "nope", "--db", str(db))
    assert result.exit_code != 0
    assert "nope" in result.output


def test_a_db_inside_a_scan_root_is_refused(destination: Path):
    result = runner.invoke(
        app,
        ["scan", "add", "S", str(destination), "--db", str(destination / "index.db")],
    )
    assert result.exit_code != 0
    assert "scan root" in result.output


def test_scan_list_refuses_to_create_a_db_inside_a_destination_like_folder(tmp_path: Path):
    destination_like = tmp_path / "destination"
    destination_like.mkdir()
    db = destination_like / "nested" / "index.db"

    result = invoke("scan", "list", "--db", str(db))

    assert result.exit_code != 0
    assert not db.exists()
    assert not db.parent.exists(), "no parent directory should be created either"


def test_scan_refresh_refuses_to_create_a_db_inside_a_destination_like_folder(tmp_path: Path):
    destination_like = tmp_path / "destination"
    destination_like.mkdir()
    db = destination_like / "nested" / "index.db"

    result = invoke("scan", "refresh", "whatever", "--db", str(db))

    assert result.exit_code != 0
    assert not db.exists()
    assert not db.parent.exists()


def test_scan_rm_refuses_to_create_a_db_inside_a_destination_like_folder(tmp_path: Path):
    destination_like = tmp_path / "destination"
    destination_like.mkdir()
    db = destination_like / "nested" / "index.db"

    result = invoke("scan", "rm", "whatever", "--db", str(db))

    assert result.exit_code != 0
    assert not db.exists()
    assert not db.parent.exists()


def test_scan_refresh_refuses_a_db_that_now_coincides_with_a_scan_root(db: Path, destination: Path):
    """An existing db, safely created elsewhere, must still be rejected if a
    later --db happens to resolve inside the scan's own roots (fix 2, part B).

    _open_index only validates against an empty roots list for scan refresh
    (the scan's roots are not known until after it is loaded), so this
    re-validates once the roots are known.
    """
    import shutil

    invoke("scan", "add", "S", str(destination), "--db", str(db))

    inside_db = destination / "index.db"
    shutil.copy(db, inside_db)

    result = invoke("scan", "refresh", "S", "--db", str(inside_db))

    assert result.exit_code != 0
    assert "scan root" in result.output


def test_scan_list_reports_a_clean_message_when_the_index_cannot_be_opened(
    db: Path, destination: Path, monkeypatch: pytest.MonkeyPatch
):
    """A locked/corrupt database must produce a clean exit-2 message, not a raw
    traceback (fix 7)."""
    invoke("scan", "add", "S", str(destination), "--db", str(db))

    def broken_open(db_path=None, roots=()):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(Index, "open", classmethod(lambda cls, *a, **k: broken_open(*a, **k)))

    result = invoke("scan", "list", "--db", str(db))

    assert result.exit_code == 2
    assert not isinstance(
        result.exception, sqlite3.Error
    ), "the sqlite error must be caught, not propagate out of the CLI"
    assert "could not access the index database" in result.output.lower()
    assert "traceback" not in result.output.lower()


def test_scan_add_reports_a_clean_message_on_a_write_failure(
    db: Path, destination: Path, monkeypatch: pytest.MonkeyPatch
):
    """An OSError while saving the scan (e.g. disk full) must produce a clean
    exit-2 message, not a raw traceback (fix 7)."""

    def broken_save_scan(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(Index, "save_scan", broken_save_scan)

    result = invoke("scan", "add", "S", str(destination), "--db", str(db))

    assert result.exit_code == 2
    assert not isinstance(
        result.exception, OSError
    ), "the OSError must be caught, not propagate out of the CLI"
    assert "could not access the index database" in result.output.lower()
    assert "traceback" not in result.output.lower()


def test_scan_add_still_creates_the_db_when_it_does_not_exist(db: Path, destination: Path):
    """scan add is the one command allowed to auto-create the index (fix 2)."""
    assert not db.exists()
    result = invoke("scan", "add", "S", str(destination), "--db", str(db))
    assert result.exit_code == 0, result.output
    assert db.exists()


def test_scan_list_json_is_machine_readable(db: Path, destination: Path):
    invoke("scan", "add", "Video SSD", str(destination), "--db", str(db))
    result = invoke("scan", "list", "--json", "--db", str(db))

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["name"] == "Video SSD"
    assert payload[0]["file_count"] == 3
    assert payload[0]["roots"] == [str(destination.resolve())]
