"""The golden test: one realistic card, every interesting bucket at once."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ftv.cli import app

runner = CliRunner()


@pytest.fixture
def world(tmp_path: Path):
    """A destination and a card exercising every bucket in one run.

    On the card:
      A.JPG           transferred cleanly            -> present
      B.MP4           transferred cleanly            -> present
      GONE.MP4        never transferred              -> missing
      DSC_0001.NEF    same name+size on the SSD, different capture time -> suspicious
      RAW_ONLY.CR3    on the scan's skip list        -> skipped_user
      .DS_Store       OS cruft                       -> skipped_junk
    """
    destination = tmp_path / "ssd"
    (destination / "2026" / "september").mkdir(parents=True)
    card = tmp_path / "card"
    clips = card / "DCIM" / "100NZ_8"
    clips.mkdir(parents=True)

    files = {
        "A.JPG": b"a" * 100,
        "B.MP4": b"b" * 200,
        "GONE.MP4": b"g" * 300,
        "DSC_0001.NEF": b"n" * 400,
        "RAW_ONLY.CR3": b"r" * 500,
    }
    for name, payload in files.items():
        (clips / name).write_bytes(payload)
    (card / ".DS_Store").write_bytes(b"junk")

    # Reorganised on the destination, proving matching is path-agnostic.
    for name in ("A.JPG", "B.MP4"):
        source = clips / name
        target = destination / "2026" / "september" / name
        target.write_bytes(source.read_bytes())
        stat = source.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime))

    # A different photo from an earlier card, same name and size.
    collision = destination / "2026" / "DSC_0001.NEF"
    collision.write_bytes(b"x" * 400)
    os.utime(collision, (1_600_000_000, 1_600_000_000))

    return {"db": tmp_path / "index.db", "destination": destination, "card": card}


def test_one_card_lands_every_bucket_correctly(world):
    add = runner.invoke(
        app,
        ["scan", "add", "Video SSD", str(world["destination"]),
         "--skip", ".CR3", "--db", str(world["db"])],
    )
    assert add.exit_code == 0, add.output

    result = runner.invoke(
        app,
        ["verify", "--scan", "Video SSD", "--path", str(world["card"]),
         "--db", str(world["db"]), "--json"],
    )
    payload = json.loads(result.output)

    assert payload["counts"]["present"] == 2
    assert payload["counts"]["missing"] == 1
    assert payload["counts"]["suspicious"] == 1
    assert payload["counts"]["skipped_user"] == 1
    assert payload["counts"]["skipped_junk"] == 1
    assert payload["counts"]["error"] == 0

    assert payload["missing"][0]["relpath"] == "DCIM/100NZ_8/GONE.MP4"
    assert payload["suspicious"][0]["relpath"] == "DCIM/100NZ_8/DSC_0001.NEF"
    assert payload["safe_to_format"] is False
    assert payload["exit_code"] == 1
    assert result.exit_code == 1


def test_every_card_file_is_accounted_for_exactly_once(world):
    runner.invoke(
        app,
        ["scan", "add", "S", str(world["destination"]), "--skip", ".CR3",
         "--db", str(world["db"])],
    )
    result = runner.invoke(
        app,
        ["verify", "--scan", "S", "--path", str(world["card"]),
         "--db", str(world["db"]), "--json"],
    )
    payload = json.loads(result.output)

    on_disk = sum(1 for p in world["card"].rglob("*") if p.is_file())
    assert sum(payload["counts"].values()) == on_disk


def test_deep_resolves_the_collision_to_missing(world):
    runner.invoke(
        app,
        ["scan", "add", "S", str(world["destination"]), "--skip", ".CR3",
         "--db", str(world["db"])],
    )
    result = runner.invoke(
        app,
        ["verify", "--scan", "S", "--path", str(world["card"]),
         "--db", str(world["db"]), "--deep", "--json"],
    )
    payload = json.loads(result.output)

    assert payload["counts"]["suspicious"] == 0
    assert payload["counts"]["missing"] == 2, "the collision is a genuinely different file"


def test_the_human_output_shows_the_folder_structure(world):
    runner.invoke(
        app,
        ["scan", "add", "S", str(world["destination"]), "--skip", ".CR3",
         "--db", str(world["db"])],
    )
    result = runner.invoke(
        app,
        ["verify", "--scan", "S", "--path", str(world["card"]), "--db", str(world["db"])],
    )

    assert "DCIM" in result.output
    assert "100NZ_8" in result.output
    assert "GONE.MP4" in result.output
    assert "do NOT format" in result.output


def test_copying_the_rest_makes_the_card_safe(world):
    runner.invoke(
        app,
        ["scan", "add", "S", str(world["destination"]), "--skip", ".CR3",
         "--db", str(world["db"])],
    )

    for name in ("GONE.MP4", "DSC_0001.NEF"):
        source = world["card"] / "DCIM" / "100NZ_8" / name
        target = world["destination"] / "2026" / "september" / name
        target.write_bytes(source.read_bytes())
        stat = source.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime))

    result = runner.invoke(
        app,
        ["verify", "--scan", "S", "--path", str(world["card"]),
         "--db", str(world["db"]), "--rescan", "--json"],
    )
    payload = json.loads(result.output)

    assert payload["safe_to_format"] is True
    assert result.exit_code == 0
