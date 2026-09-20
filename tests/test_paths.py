from pathlib import Path

import pytest

from ftv.paths import DbPathError, default_db_path, validate_db_path


def test_default_db_path_is_under_a_user_data_dir():
    path = default_db_path()
    assert path.name == "index.db"
    assert path.is_absolute()
    assert "ftv" in str(path)


def test_a_db_outside_every_scan_root_is_accepted(tmp_path: Path):
    root = tmp_path / "ssd"
    root.mkdir()
    validate_db_path(tmp_path / "data" / "index.db", [root])


def test_a_db_inside_a_scan_root_is_rejected(tmp_path: Path):
    root = tmp_path / "ssd"
    (root / "nested").mkdir(parents=True)
    with pytest.raises(DbPathError, match="inside a scan root"):
        validate_db_path(root / "nested" / "index.db", [root])


def test_a_db_at_a_scan_root_itself_is_rejected(tmp_path: Path):
    root = tmp_path / "ssd"
    root.mkdir()
    with pytest.raises(DbPathError, match="inside a scan root"):
        validate_db_path(root / "index.db", [root])


def test_a_db_on_a_removable_volume_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ftv.paths.REMOVABLE_PREFIXES", (str(tmp_path / "Volumes"),))
    card = tmp_path / "Volumes" / "NIKON Z8"
    card.mkdir(parents=True)
    with pytest.raises(DbPathError, match="removable volume"):
        validate_db_path(card / "index.db", [])


def test_validation_passes_when_there_are_no_roots(tmp_path: Path):
    validate_db_path(tmp_path / "index.db", [])


def test_a_root_that_does_not_exist_does_not_break_validation(tmp_path: Path):
    validate_db_path(tmp_path / "index.db", [tmp_path / "gone"])
