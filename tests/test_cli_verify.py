import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ftv.cli import app

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path):
    """A destination holding two of the card's three files."""
    import os

    destination = tmp_path / "ssd"
    destination.mkdir()
    card = tmp_path / "card"
    (card / "DCIM" / "100NZ_8").mkdir(parents=True)

    for name, payload in (("A.JPG", b"a" * 10), ("B.MP4", b"b" * 20), ("C.NEF", b"c" * 30)):
        (card / "DCIM" / "100NZ_8" / name).write_bytes(payload)

    for name in ("A.JPG", "B.MP4"):
        source = card / "DCIM" / "100NZ_8" / name
        target = destination / name
        target.write_bytes(source.read_bytes())
        stat = source.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime))

    return {"db": tmp_path / "index.db", "destination": destination, "card": card}


def add_scan(workspace, *extra: str):
    return runner.invoke(
        app,
        [
            "scan", "add", "Video SSD", str(workspace["destination"]),
            "--db", str(workspace["db"]), *extra,
        ],
    )


def verify(workspace, *extra: str):
    return runner.invoke(
        app,
        [
            "verify",
            "--scan", "Video SSD",
            "--path", str(workspace["card"]),
            "--db", str(workspace["db"]),
            *extra,
        ],
    )


def test_one_shot_verify_reports_the_missing_file_and_fails(workspace):
    add_scan(workspace)
    result = verify(workspace)

    assert result.exit_code == 1
    assert "C.NEF" in result.output
    assert "100NZ_8" in result.output, "the folder structure leading to it must be shown"


def test_one_shot_verify_succeeds_when_everything_transferred(workspace):
    import os
    import shutil

    source = workspace["card"] / "DCIM" / "100NZ_8" / "C.NEF"
    target = workspace["destination"] / "C.NEF"
    shutil.copy2(source, target)
    os.utime(target, (source.stat().st_atime, source.stat().st_mtime))

    add_scan(workspace)
    result = verify(workspace)

    assert result.exit_code == 0, result.output
    assert "safe to format" in result.output.lower()


def test_a_skip_listed_extension_makes_the_card_safe(workspace):
    add_scan(workspace, "--skip", ".NEF")
    result = verify(workspace)

    assert result.exit_code == 0, result.output
    assert "safe to format" in result.output.lower()


def test_a_per_run_skip_override_applies(workspace):
    add_scan(workspace)
    result = verify(workspace, "--skip", ".NEF")

    assert result.exit_code == 0, result.output


def test_json_output_is_machine_readable(workspace):
    add_scan(workspace)
    result = verify(workspace, "--json")

    payload = json.loads(result.output)
    assert payload["safe_to_format"] is False
    assert payload["counts"]["missing"] == 1
    assert payload["missing"][0]["relpath"] == "DCIM/100NZ_8/C.NEF"
    assert result.exit_code == 1


def test_verify_reports_an_unknown_scan_name(workspace):
    result = verify(workspace)
    assert result.exit_code != 0
    assert "Video SSD" in result.output


def test_verify_refuses_when_the_destination_is_not_mounted(workspace):
    import shutil

    add_scan(workspace)
    shutil.rmtree(workspace["destination"])

    result = verify(workspace)
    assert result.exit_code != 0
    assert "not available" in result.output.lower()


def test_verify_refuses_a_card_path_that_is_not_a_folder(workspace, tmp_path: Path):
    add_scan(workspace)
    result = runner.invoke(
        app,
        ["verify", "--scan", "Video SSD", "--path", str(tmp_path / "nope"),
         "--db", str(workspace["db"])],
    )
    assert result.exit_code != 0
    assert "nope" in result.output


def test_rescan_picks_up_files_copied_after_the_scan(workspace):
    import os
    import shutil

    add_scan(workspace)
    assert verify(workspace).exit_code == 1

    source = workspace["card"] / "DCIM" / "100NZ_8" / "C.NEF"
    target = workspace["destination"] / "C.NEF"
    shutil.copy2(source, target)
    os.utime(target, (source.stat().st_atime, source.stat().st_mtime))

    assert verify(workspace).exit_code == 1, "the stale scan still reports it missing"
    assert verify(workspace, "--rescan").exit_code == 0, "--rescan refreshes first"


def test_deep_flag_defaults_to_the_suspicious_scope(workspace):
    add_scan(workspace)
    result = verify(workspace, "--deep")
    assert result.exit_code == 1, result.output


def test_deep_all_catches_a_same_size_different_content_copy(workspace):
    import os

    add_scan(workspace)
    target = workspace["destination"] / "A.JPG"
    mtime = target.stat().st_mtime
    target.write_bytes(b"z" * 10)
    os.utime(target, (mtime, mtime))

    shallow = verify(workspace, "--skip", ".NEF")
    assert shallow.exit_code == 0, "name+size+mtime cannot see this"

    deep = verify(workspace, "--skip", ".NEF", "--deep-all")
    assert deep.exit_code == 1
    assert "A.JPG" in deep.output


def test_verify_prints_the_scans_age(workspace):
    add_scan(workspace)
    result = verify(workspace)
    assert "just now" in result.output or "ago" in result.output
