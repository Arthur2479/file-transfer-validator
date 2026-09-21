"""Proves the tool modified nothing on the card or the destination.

This is the executable form of the project's primary constraint. It records a
full manifest of both trees, runs the heaviest verification path available
(--deep-all, the only path that opens file contents), then re-records and
asserts nothing changed.
"""

import hashlib
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ftv.cli import app

runner = CliRunner()


def manifest(root: Path) -> dict[str, tuple]:
    """Every path under root with its size, mtime and content digest."""
    out: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        stat = path.lstat()
        if path.is_dir() and not path.is_symlink():
            out[rel] = ("dir", stat.st_mtime_ns, stat.st_mode)
        elif path.is_symlink():
            out[rel] = ("link", os.readlink(path))
        else:
            out[rel] = (
                "file",
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_mode,
                hashlib.blake2b(path.read_bytes()).hexdigest(),
            )
    return out


@pytest.fixture
def world(tmp_path: Path):
    destination = tmp_path / "ssd"
    (destination / "2026").mkdir(parents=True)
    card = tmp_path / "card"
    clips = card / "DCIM" / "100NZ_8"
    clips.mkdir(parents=True)

    for name, payload in (
        ("A.JPG", b"a" * 100),
        ("B.MP4", b"b" * 200),
        ("MISSING.MP4", b"m" * 300),
        ("RAW.CR3", b"r" * 400),
    ):
        (clips / name).write_bytes(payload)
    (card / ".DS_Store").write_bytes(b"junk")
    (card / "MISC").mkdir()
    (card / "MISC" / "AUTPRINT.MRK").write_bytes(b"x")

    for name in ("A.JPG", "B.MP4"):
        source = clips / name
        target = destination / "2026" / name
        target.write_bytes(source.read_bytes())
        stat = source.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime))

    return {"db": tmp_path / "index.db", "destination": destination, "card": card}


def test_a_full_verification_modifies_nothing(world):
    """The heaviest path: scan, refresh, verify, and hash every matched pair."""
    runner.invoke(
        app,
        ["scan", "add", "S", str(world["destination"]), "--skip", ".CR3",
         "--db", str(world["db"])],
    )

    card_before = manifest(world["card"])
    destination_before = manifest(world["destination"])

    runner.invoke(app, ["scan", "refresh", "S", "--db", str(world["db"])])
    runner.invoke(
        app,
        ["verify", "--scan", "S", "--path", str(world["card"]),
         "--db", str(world["db"]), "--deep-all"],
    )
    runner.invoke(app, ["scan", "list", "--db", str(world["db"])])

    assert manifest(world["card"]) == card_before, "the card was modified"
    assert manifest(world["destination"]) == destination_before, "the destination was modified"


def test_verification_creates_no_new_paths(world):
    runner.invoke(app, ["scan", "add", "S", str(world["destination"]), "--db", str(world["db"])])

    card_paths = set(p for p in world["card"].rglob("*"))
    destination_paths = set(p for p in world["destination"].rglob("*"))

    runner.invoke(
        app,
        ["verify", "--scan", "S", "--path", str(world["card"]),
         "--db", str(world["db"]), "--deep-all"],
    )

    assert set(world["card"].rglob("*")) == card_paths
    assert set(world["destination"].rglob("*")) == destination_paths


def test_the_index_is_the_only_thing_written(world, tmp_path: Path):
    """Nothing outside the database file appears or changes."""
    runner.invoke(app, ["scan", "add", "S", str(world["destination"]), "--db", str(world["db"])])
    assert world["db"].exists()

    before = manifest(world["card"]) | manifest(world["destination"])
    runner.invoke(
        app,
        ["verify", "--scan", "S", "--path", str(world["card"]),
         "--db", str(world["db"]), "--deep-all"],
    )
    after = manifest(world["card"]) | manifest(world["destination"])

    assert after == before


def test_a_read_only_card_verifies_without_error(world):
    """A card mounted read-only must still verify cleanly."""
    runner.invoke(app, ["scan", "add", "S", str(world["destination"]), "--db", str(world["db"])])

    clips = world["card"] / "DCIM" / "100NZ_8"
    original_modes = {p: p.stat().st_mode for p in clips.rglob("*")}
    for path in clips.rglob("*"):
        path.chmod(0o444)
    try:
        result = runner.invoke(
            app,
            ["verify", "--scan", "S", "--path", str(world["card"]),
             "--db", str(world["db"]), "--deep-all", "--json"],
        )
        assert result.exit_code in (0, 1), result.output
        assert '"error": 0' in result.output or '"error":0' in result.output
    finally:
        for path, mode in original_modes.items():
            path.chmod(mode)
