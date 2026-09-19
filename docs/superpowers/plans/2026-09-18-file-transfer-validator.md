# file-transfer-validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only CLI that verifies every file on a connected SD card is already present in a remembered scan of a destination folder, so the card can be formatted with confidence.

**Architecture:** A pure comparison core (`compare()` takes card file entries plus a `Lookup` protocol and returns a `VerifyReport` — no filesystem, no database, no terminal) wrapped by thin I/O layers. A SQLite index remembers destination scans so each card resolves via indexed lookups plus one `stat` per card file, rather than a full walk of the destination. All card and destination filesystem access funnels through a single read-only module.

**Tech Stack:** Python 3.12+ (3.14 available), Poetry 2.x with PEP 621 `[project]` layout, Typer (CLI), Rich (output), platformdirs (paths), SQLite (stdlib), pytest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-18-file-transfer-validator-design.md`

## Global Constraints

- **Read-only is the primary constraint and outranks performance and convenience.** The tool never modifies any file or directory on an SD card or in a destination folder. The only file it ever opens for writing is its own index database.
- `src/ftv/core/fsread.py` is the ONLY module permitted to touch card or destination paths. It exposes exactly `walk()`, `stat_file()`, `read_chunks()` and opens files only with mode `"rb"`.
- No module in `src/ftv/core/` other than `index.py` may contain write-capable filesystem calls. Task 2 adds an AST-based audit test that enforces this on every run.
- The index database lives under `platformdirs.user_data_dir("ftv")` and must never be placed inside a scan root or on a removable volume.
- Matching is **path-agnostic**: basename plus byte size only. Never compare paths relative to the card root.
- Exit code `0` requires `missing`, `suspicious`, AND `error` to all be empty. Anything unresolved blocks the green verdict.
- Python `>=3.12`. Dependencies limited to: typer, rich, platformdirs (runtime); pytest, ruff (dev).
- Frozen dataclasses with `slots=True` for all models. TDD throughout: failing test first, always.

---

### Task 1: Project scaffold and core models

**Files:**
- Create: `pyproject.toml`
- Create: `src/ftv/__init__.py`
- Create: `src/ftv/core/__init__.py`
- Create: `src/ftv/core/models.py`
- Test: `tests/__init__.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces: `FileEntry(path, relpath, name, size, mtime)`, `IndexedFile(path, name, size, mtime)`, `Bucket` enum, `Verdict(entry, bucket, matched, reason)`, `WalkError(path, message)`, `Scan(name, roots, scanned_at, use_mtime, skip_extensions, file_count)`, `VerifyReport(scan_name, card_root, verdicts, skipped_user, skipped_junk, errors)` with `.present/.suspicious/.missing/.safe_to_format/.exit_code/.counts()`, and the `Lookup` protocol.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "file-transfer-validator"
version = "0.1.0"
description = "Verify every file on an SD card was transferred before formatting it."
requires-python = ">=3.12"
dependencies = [
    "typer>=0.15,<0.16",
    "rich>=13.9,<15",
    "platformdirs>=4.3,<5",
]

[project.scripts]
ftv = "ftv.cli:app"

[tool.poetry]
packages = [{ include = "ftv", from = "src" }]

[tool.poetry.group.dev.dependencies]
pytest = "^8.3"
ruff = "^0.8"

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[build-system]
requires = ["poetry-core>=2.0.0"]
build-backend = "poetry.core.masonry.api"
```

- [ ] **Step 2: Install and verify the toolchain**

```bash
mkdir -p src/ftv/core tests
touch src/ftv/__init__.py src/ftv/core/__init__.py tests/__init__.py
poetry install
poetry run pytest --version
poetry run ruff --version
```

Expected: `poetry install` succeeds and installs the project in editable mode; both version commands print a version.

- [ ] **Step 3: Write the failing test**

Create `tests/test_models.py`:

```python
import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ftv.core.models import (
    Bucket,
    FileEntry,
    IndexedFile,
    Scan,
    Verdict,
    VerifyReport,
    WalkError,
)


def entry(name: str = "IMG_0001.JPG", size: int = 100, mtime: float = 1000.0) -> FileEntry:
    return FileEntry(
        path=Path("/Volumes/CARD/DCIM/100NZ_8") / name,
        relpath=f"DCIM/100NZ_8/{name}",
        name=name,
        size=size,
        mtime=mtime,
    )


def test_file_entry_is_frozen():
    e = entry()
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.size = 1  # type: ignore[misc]


def test_report_groups_verdicts_by_bucket():
    present = Verdict(entry=entry("A.JPG"), bucket=Bucket.PRESENT)
    missing = Verdict(entry=entry("B.JPG"), bucket=Bucket.MISSING)
    suspicious = Verdict(entry=entry("C.JPG"), bucket=Bucket.SUSPICIOUS, reason="mtime differs")
    report = VerifyReport(
        scan_name="Video SSD",
        card_root=Path("/Volumes/CARD"),
        verdicts=(present, missing, suspicious),
    )

    assert report.present == (present,)
    assert report.missing == (missing,)
    assert report.suspicious == (suspicious,)


def test_report_is_safe_only_when_nothing_is_unresolved():
    clean = VerifyReport(
        scan_name="s",
        card_root=Path("/Volumes/CARD"),
        verdicts=(Verdict(entry=entry(), bucket=Bucket.PRESENT),),
        skipped_user=("DCIM/100NZ_8/DSC_0001.NEF",),
        skipped_junk=(".DS_Store",),
    )
    assert clean.safe_to_format is True
    assert clean.exit_code == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"verdicts": (Verdict(entry=entry(), bucket=Bucket.MISSING),)},
        {"verdicts": (Verdict(entry=entry(), bucket=Bucket.SUSPICIOUS),)},
        {
            "verdicts": (),
            "errors": (WalkError(path=Path("/Volumes/CARD/x"), message="permission denied"),),
        },
    ],
)
def test_report_is_not_safe_when_anything_is_unresolved(kwargs):
    report = VerifyReport(scan_name="s", card_root=Path("/Volumes/CARD"), **kwargs)
    assert report.safe_to_format is False
    assert report.exit_code == 1


def test_counts_covers_every_bucket_exactly_once():
    report = VerifyReport(
        scan_name="s",
        card_root=Path("/Volumes/CARD"),
        verdicts=(
            Verdict(entry=entry("A.JPG"), bucket=Bucket.PRESENT),
            Verdict(entry=entry("B.JPG"), bucket=Bucket.MISSING),
        ),
        skipped_user=("C.NEF",),
        skipped_junk=(".DS_Store", "MISC/AUTPRINT.MRK"),
        errors=(WalkError(path=Path("/Volumes/CARD/bad"), message="io error"),),
    )
    counts = report.counts()

    assert set(counts) == set(Bucket)
    assert counts[Bucket.PRESENT] == 1
    assert counts[Bucket.MISSING] == 1
    assert counts[Bucket.SKIPPED_USER] == 1
    assert counts[Bucket.SKIPPED_JUNK] == 2
    assert counts[Bucket.ERROR] == 1
    assert sum(counts.values()) == report.total_files


def test_scan_holds_multiple_roots_and_a_skip_list():
    scan = Scan(
        name="Video SSD",
        roots=(Path("/Volumes/SSD/Video"), Path("/Volumes/SSD/Photo")),
        scanned_at=datetime(2026, 9, 14, 10, 0, tzinfo=UTC),
        use_mtime=True,
        skip_extensions=frozenset({".nef"}),
        file_count=48213,
    )
    assert len(scan.roots) == 2
    assert ".nef" in scan.skip_extensions


def test_indexed_file_carries_its_destination_path():
    f = IndexedFile(path="/Volumes/SSD/Video/2026/IMG_0001.JPG", name="IMG_0001.JPG", size=100, mtime=1000.0)
    assert f.name == "IMG_0001.JPG"
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `poetry run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.core.models'`

- [ ] **Step 5: Write the implementation**

Create `src/ftv/core/models.py`:

```python
"""Immutable data types shared across the tool.

These types carry no behaviour that touches the filesystem, the database, or
the terminal, so every other module can depend on them freely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class Bucket(str, Enum):
    """The outcome for a single card file. Every card file lands in exactly one."""

    PRESENT = "present"
    SUSPICIOUS = "suspicious"
    MISSING = "missing"
    SKIPPED_USER = "skipped_user"
    SKIPPED_JUNK = "skipped_junk"
    ERROR = "error"


#: Buckets that block a "safe to format" verdict.
UNRESOLVED = (Bucket.MISSING, Bucket.SUSPICIOUS, Bucket.ERROR)


@dataclass(frozen=True, slots=True)
class FileEntry:
    """A file found on a card or in a destination root."""

    path: Path
    relpath: str
    name: str
    size: int
    mtime: float


@dataclass(frozen=True, slots=True)
class IndexedFile:
    """A file recorded in a remembered scan of a destination."""

    path: str
    name: str
    size: int
    mtime: float


@dataclass(frozen=True, slots=True)
class WalkError:
    """A path that could not be read or stat'ed."""

    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class Verdict:
    """The comparison outcome for one card file that was actually checked."""

    entry: FileEntry
    bucket: Bucket
    matched: IndexedFile | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Scan:
    """A remembered scan of one or more destination roots."""

    name: str
    roots: tuple[Path, ...]
    scanned_at: datetime
    use_mtime: bool = True
    skip_extensions: frozenset[str] = frozenset()
    file_count: int = 0


@dataclass(frozen=True, slots=True)
class VerifyReport:
    """The full outcome of checking one card against one scan.

    ``verdicts`` holds the files that were actually compared. Skipped files and
    errors get dedicated fields because they carry no match and may have no
    stat, but ``counts()`` still reports all six buckets so the "exactly one
    bucket per file" invariant stays checkable.
    """

    scan_name: str
    card_root: Path
    verdicts: tuple[Verdict, ...] = ()
    skipped_user: tuple[str, ...] = ()
    skipped_junk: tuple[str, ...] = ()
    errors: tuple[WalkError, ...] = ()

    def of(self, bucket: Bucket) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.bucket is bucket)

    @property
    def present(self) -> tuple[Verdict, ...]:
        return self.of(Bucket.PRESENT)

    @property
    def suspicious(self) -> tuple[Verdict, ...]:
        return self.of(Bucket.SUSPICIOUS)

    @property
    def missing(self) -> tuple[Verdict, ...]:
        return self.of(Bucket.MISSING)

    @property
    def checked_count(self) -> int:
        return len(self.verdicts)

    @property
    def total_files(self) -> int:
        return (
            len(self.verdicts)
            + len(self.skipped_user)
            + len(self.skipped_junk)
            + len(self.errors)
        )

    def counts(self) -> dict[Bucket, int]:
        counts = {bucket: 0 for bucket in Bucket}
        for verdict in self.verdicts:
            counts[verdict.bucket] += 1
        counts[Bucket.SKIPPED_USER] = len(self.skipped_user)
        counts[Bucket.SKIPPED_JUNK] = len(self.skipped_junk)
        counts[Bucket.ERROR] = len(self.errors)
        return counts

    @property
    def safe_to_format(self) -> bool:
        """True only when nothing was left unresolved.

        Deliberately conservative: an unreadable file or an ambiguous match
        blocks the green verdict rather than being rounded down to acceptable.
        """
        return not (self.missing or self.suspicious or self.errors)

    @property
    def exit_code(self) -> int:
        return 0 if self.safe_to_format else 1


@runtime_checkable
class Lookup(Protocol):
    """Answers "is there a file with this basename and size in the scan?"."""

    def find(self, name: str, size: int) -> list[IndexedFile]: ...
```


- [ ] **Step 6: Run the test to verify it passes**

Run: `poetry run pytest tests/test_models.py -v && poetry run ruff check .`
Expected: all tests PASS, Ruff reports no issues.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml poetry.lock src/ftv tests
git commit -m "feat: project scaffold and core models

Frozen dataclasses for the comparison pipeline, plus the safe_to_format
rule: exit 0 requires missing, suspicious and error all empty.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Read-only filesystem choke point and the invariant audit

**Files:**
- Create: `src/ftv/core/fsread.py`
- Test: `tests/test_fsread.py`, `tests/test_readonly_invariant.py`

**Interfaces:**
- Consumes: `WalkError` from `ftv.core.models` (Task 1)
- Produces: `FoundFile(path, relpath)`, `stat_file(path) -> tuple[int, float]`, `read_chunks(path, chunk_size=1048576) -> Iterator[bytes]`, `walk(root, prune=None) -> Iterator[FoundFile | WalkError]`. Every later task that touches a card or destination path uses these and nothing else.

This task also delivers the audit test that enforces the read-only invariant across the whole `core` package for the rest of the project. It is AST-based rather than text-based, so it cannot be fooled by formatting and cannot drift silently.

- [ ] **Step 1: Write the failing tests for `fsread`**

Create `tests/test_fsread.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/test_fsread.py -v`
Expected: FAIL — `ImportError: cannot import name 'fsread' from 'ftv.core'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/core/fsread.py`:

```python
"""The only module permitted to touch card or destination paths.

Every filesystem operation the tool performs on user data goes through here,
and everything here is read-only: directory listing, stat, and reading bytes.
Concentrating access in one small module is what makes the read-only invariant
auditable — see tests/test_readonly_invariant.py.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ftv.core.models import WalkError

DEFAULT_CHUNK_SIZE = 1 << 20


@dataclass(frozen=True, slots=True)
class FoundFile:
    """A file located by :func:`walk`."""

    path: Path
    relpath: str


def stat_file(path: Path) -> tuple[int, float]:
    """Return ``(size, mtime)`` for ``path``. Raises OSError on failure."""
    st = path.stat()
    return st.st_size, st.st_mtime


def read_chunks(path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
    """Stream ``path`` in binary chunks.

    Opened with mode ``"rb"`` only; the audit test rejects any other mode
    anywhere in this package.
    """
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return
            yield chunk


def walk(
    root: Path,
    prune: Callable[[str], bool] | None = None,
) -> Iterator[FoundFile | WalkError]:
    """Yield every regular file under ``root``, plus a WalkError per failure.

    ``prune`` is called with each directory's basename; returning True skips
    that directory and everything beneath it. Symlinks are never followed, so
    a link loop cannot hang the walk and a link out of the tree cannot pull
    unrelated files into the comparison.

    Failures are yielded rather than raised: one unreadable directory must not
    discard the verdict for the rest of the card.
    """
    root = root.resolve()
    if not root.is_dir():
        yield WalkError(path=root, message="not a readable directory")
        return

    def on_error(exc: OSError) -> None:
        errors.append(WalkError(path=Path(exc.filename or root), message=str(exc.strerror or exc)))

    errors: list[WalkError] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if not (current / d).is_symlink() and not (prune(d) if prune else False)
        ]
        for filename in filenames:
            candidate = current / filename
            if candidate.is_symlink() or not candidate.is_file():
                continue
            yield FoundFile(path=candidate, relpath=candidate.relative_to(root).as_posix())
        while errors:
            yield errors.pop()
    while errors:
        yield errors.pop()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/test_fsread.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the read-only invariant audit test**

Create `tests/test_readonly_invariant.py`:

```python
"""Enforces the project's primary constraint: core never writes user data.

This walks the AST of every module in ftv.core (except index.py, which owns the
database) looking for write-capable filesystem calls and for any open() whose
mode is not exactly "rb". It runs on every test invocation, so the invariant
cannot quietly rot as the code grows.
"""

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "src" / "ftv" / "core"

#: index.py owns the index database and is the one module allowed to write.
EXEMPT = {"index.py"}

FORBIDDEN_NAMES = frozenset(
    {
        "chmod",
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "link",
        "lchmod",
        "makedirs",
        "mkdir",
        "mkfifo",
        "move",
        "remove",
        "removedirs",
        "rename",
        "renames",
        "replace",
        "rmdir",
        "rmtree",
        "symlink",
        "touch",
        "truncate",
        "unlink",
        "utime",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
    }
)


def core_modules() -> list[Path]:
    modules = [p for p in sorted(CORE.glob("*.py")) if p.name not in EXEMPT]
    assert modules, f"no core modules found under {CORE}"
    return modules


def test_core_contains_no_write_capable_calls():
    offenders = []
    for path in core_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
                offenders.append(f"{path.name}:{node.lineno}: .{node.attr}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                offenders.append(f"{path.name}:{node.lineno}: {node.id}")
    assert offenders == [], (
        "write-capable filesystem calls found in the read-only core: " + "; ".join(offenders)
    )


def test_core_never_imports_shutil():
    offenders = []
    for path in core_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.name}:{node.lineno}" for a in node.names if a.name == "shutil"
                ]
            elif isinstance(node, ast.ImportFrom) and node.module == "shutil":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], "shutil imported in read-only core: " + "; ".join(offenders)


def _open_mode(node: ast.Call) -> object:
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        return node.args[1].value
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def test_core_opens_files_read_binary_only():
    offenders = []
    for path in core_modules():
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_open = (isinstance(func, ast.Name) and func.id == "open") or (
                isinstance(func, ast.Attribute) and func.attr == "open"
            )
            if is_open and _open_mode(node) != "rb":
                offenders.append(f"{path.name}:{node.lineno}: open(mode={_open_mode(node)!r})")
    assert offenders == [], "non-read-binary open() in core: " + "; ".join(offenders)


def test_only_fsread_performs_directory_walks():
    offenders = []
    for path in core_modules():
        if path.name == "fsread.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"walk", "scandir", "iterdir", "rglob", "glob"}:
                offenders.append(f"{path.name}:{node.lineno}: .{node.attr}")
    assert offenders == [], (
        "directory traversal outside fsread.py: " + "; ".join(offenders)
    )
```

- [ ] **Step 6: Run the audit test to verify it passes against the current core**

Run: `poetry run pytest tests/test_readonly_invariant.py -v`
Expected: all PASS. `fsread.py` uses only `os.walk`, `stat`, and `open(..., "rb")`.

- [ ] **Step 7: Prove the audit test actually catches a violation**

Temporarily append to `src/ftv/core/fsread.py`:

```python
def _violation(path: Path) -> None:
    path.write_text("nope")
```

Run: `poetry run pytest tests/test_readonly_invariant.py::test_core_contains_no_write_capable_calls -v`
Expected: FAIL, naming `fsread.py` and `.write_text`. **Then delete `_violation` and re-run to confirm it passes again.** An invariant test that has never failed is not known to work.

- [ ] **Step 8: Commit**

```bash
git add src/ftv/core/fsread.py tests/test_fsread.py tests/test_readonly_invariant.py
git commit -m "feat: read-only filesystem choke point with AST invariant audit

fsread is the only module that touches card or destination paths: walk,
stat, and read-binary, with failures yielded rather than raised so one
unreadable directory cannot discard a card's verdict.

The audit test parses every core module and rejects write-capable calls,
shutil imports, non-rb open() modes, and traversal outside fsread.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: File filters — junk denylist and per-scan skip list

**Files:**
- Create: `src/ftv/core/filters.py`
- Test: `tests/test_filters.py`

**Interfaces:**
- Consumes: nothing beyond stdlib
- Produces: `Decision` enum (`KEEP`, `SKIP_USER`, `SKIP_JUNK`), `normalize_extension(value) -> str`, `normalize_skip_list(values) -> frozenset[str]`, `is_junk_dir(name) -> bool`, `decide(relpath, skip_extensions) -> Decision`, and the `JUNK_DIRS` / `JUNK_NAMES` / `JUNK_SUFFIXES` constants.

The spec rejects an extension allowlist: shooting a format that is not on the list would make those files invisible to the check. So `decide` returns `KEEP` for anything it does not positively recognise as junk or as user-skipped.

- [ ] **Step 1: Write the failing test**

Create `tests/test_filters.py`:

```python
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


@pytest.mark.parametrize("name", ["MISC", "misc", ".fseventsd", "System Volume Information", ".Trashes"])
def test_junk_directories_are_recognised_for_pruning(name):
    assert is_junk_dir(name) is True


@pytest.mark.parametrize("name", ["DCIM", "100NZ_8", "PRIVATE", "Video"])
def test_real_directories_are_not_pruned(name):
    assert is_junk_dir(name) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_filters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.core.filters'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/core/filters.py`:

```python
"""Decides which card files the tool holds accountable.

Deliberately a denylist, not an allowlist: a file type nobody configured must
still be checked, because an unchecked file type is a silent path to a wrong
"safe to format" verdict.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from pathlib import PurePosixPath


class Decision(Enum):
    KEEP = "keep"
    SKIP_USER = "skip_user"
    SKIP_JUNK = "skip_junk"


#: Directories that hold camera or OS bookkeeping, never captured media.
JUNK_DIRS = frozenset(
    {
        ".fseventsd",
        ".spotlight-v100",
        ".temporaryitems",
        ".trashes",
        "misc",
        "system volume information",
    }
)

#: Exact filenames that are never captured media.
JUNK_NAMES = frozenset({".ds_store", "autprint.mrk", "desktop.ini", "thumbs.db"})

#: Extensions that are camera bookkeeping (thumbnails, catalog files).
JUNK_SUFFIXES = frozenset({".thm", ".ctg", ".mrk", ".bin", ".modd", ".moff"})


def normalize_extension(value: str) -> str:
    """Normalise an extension as typed into ``.ext`` lowercase form."""
    cleaned = value.strip().lstrip("*").strip()
    if cleaned.startswith("."):
        cleaned = cleaned[1:]
    cleaned = cleaned.strip().lower()
    if not cleaned:
        raise ValueError("extension is empty")
    return f".{cleaned}"


def normalize_skip_list(values: Iterable[str] | None) -> frozenset[str]:
    """Normalise a skip list, accepting repeated flags and comma-separated values."""
    if not values:
        return frozenset()
    out: set[str] = set()
    for value in values:
        for part in value.split(","):
            if part.strip():
                out.add(normalize_extension(part))
    return frozenset(out)


def is_junk_dir(name: str) -> bool:
    """True when a directory basename should be pruned from the walk."""
    return name.lower() in JUNK_DIRS


def decide(relpath: str, skip_extensions: frozenset[str]) -> Decision:
    """Classify a card-relative path.

    Junk is checked before the user skip list so that reporting never credits
    camera cruft to a deliberate user decision.
    """
    parts = PurePosixPath(relpath).parts
    if any(is_junk_dir(part) for part in parts[:-1]):
        return Decision.SKIP_JUNK

    name = parts[-1] if parts else relpath
    lowered = name.lower()
    if lowered in JUNK_NAMES or lowered.startswith("._"):
        return Decision.SKIP_JUNK

    suffix = PurePosixPath(lowered).suffix
    if suffix and suffix in JUNK_SUFFIXES:
        return Decision.SKIP_JUNK
    if suffix and suffix in skip_extensions:
        return Decision.SKIP_USER
    return Decision.KEEP
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_filters.py -v && poetry run ruff check .`
Expected: all PASS, Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/ftv/core/filters.py tests/test_filters.py
git commit -m "feat: junk denylist and per-scan skip list

Denylist rather than allowlist, so an unconfigured file type is still
checked. Junk is classified before the user skip list so reporting never
credits camera cruft to a deliberate skip.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Walker — tree to classified file entries

**Files:**
- Create: `src/ftv/core/walker.py`
- Test: `tests/test_walker.py`

**Interfaces:**
- Consumes: `fsread.walk`, `fsread.stat_file`, `fsread.FoundFile` (Task 2); `filters.decide`, `filters.is_junk_dir`, `filters.Decision` (Task 3); `FileEntry`, `WalkError` (Task 1)
- Produces: `WalkOutcome(entries, skipped_user, skipped_junk, errors)` and `walk_tree(root, skip_extensions) -> WalkOutcome`. Used by both the scan side (Task 5) and the card side (Task 9).

- [ ] **Step 1: Write the failing test**

Create `tests/test_walker.py`:

```python
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
    assert sorted(outcome.skipped_junk) == [
        ".DS_Store",
        "DCIM/100NZ_8/DSC_0001.THM",
        "MISC/AUTPRINT.MRK",
    ]
    assert outcome.skipped_user == ()


def test_user_skip_list_moves_entries_out_of_the_checked_set(card: Path):
    outcome = walk_tree(card, frozenset({".nef"}))
    assert [e.name for e in outcome.entries] == ["DSC_0001.JPG"]
    assert outcome.skipped_user == ("DCIM/100NZ_8/DSC_0001.NEF",)


def test_every_file_is_accounted_for_exactly_once(card: Path):
    outcome = walk_tree(card, frozenset({".nef"}))
    on_disk = sum(1 for p in card.rglob("*") if p.is_file())
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_walker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.core.walker'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/core/walker.py`:

```python
"""Turns a directory tree into classified file entries.

Used for both sides of the comparison: to build a scan of a destination, and
to enumerate a card at verify time. Sharing this module is what guarantees both
sides apply an identical notion of which files count.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ftv.core import filters, fsread
from ftv.core.filters import Decision
from ftv.core.models import FileEntry, WalkError


@dataclass(frozen=True, slots=True)
class WalkOutcome:
    """Every file under a root, sorted into what gets checked and what does not."""

    entries: tuple[FileEntry, ...] = ()
    skipped_user: tuple[str, ...] = ()
    skipped_junk: tuple[str, ...] = ()
    errors: tuple[WalkError, ...] = ()

    @property
    def total_files(self) -> int:
        return (
            len(self.entries)
            + len(self.skipped_user)
            + len(self.skipped_junk)
            + len(self.errors)
        )


def walk_tree(root: Path, skip_extensions: frozenset[str]) -> WalkOutcome:
    """Walk ``root`` and classify every file found.

    Junk directories are pruned during the walk rather than filtered afterwards,
    so a large ``System Volume Information`` tree costs nothing. Files that
    cannot be stat'ed become errors instead of vanishing, because a file the
    tool could not measure must never be implied to be backed up.
    """
    entries: list[FileEntry] = []
    skipped_user: list[str] = []
    skipped_junk: list[str] = []
    errors: list[WalkError] = []

    for found in fsread.walk(root, prune=filters.is_junk_dir):
        if isinstance(found, WalkError):
            errors.append(found)
            continue

        decision = filters.decide(found.relpath, skip_extensions)
        if decision is Decision.SKIP_JUNK:
            skipped_junk.append(found.relpath)
            continue
        if decision is Decision.SKIP_USER:
            skipped_user.append(found.relpath)
            continue

        try:
            size, mtime = fsread.stat_file(found.path)
        except OSError as exc:
            errors.append(
                WalkError(path=found.path, message=str(exc.strerror or exc))
            )
            continue

        entries.append(
            FileEntry(
                path=found.path,
                relpath=found.relpath,
                name=found.path.name,
                size=size,
                mtime=mtime,
            )
        )

    return WalkOutcome(
        entries=tuple(entries),
        skipped_user=tuple(skipped_user),
        skipped_junk=tuple(skipped_junk),
        errors=tuple(errors),
    )
```

Note on the pruning test: because `is_junk_dir("MISC")` prunes the directory, files beneath it never reach `decide`. The walker therefore reports the pruned directory's own files only when they were already yielded — pruning means `MISC/AUTPRINT.MRK` is reported as junk only if `MISC` was not pruned first. Make the test reflect the real behaviour: pruned trees contribute nothing at all. **If `test_junk_is_reported_as_relpaths_not_entries` fails on `MISC/AUTPRINT.MRK`, that is correct behaviour — update the expected list to `[".DS_Store", "DCIM/100NZ_8/DSC_0001.THM"]` and adjust `test_every_file_is_accounted_for_exactly_once` to exclude files under junk directories from `on_disk`.**

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_walker.py -v`
Expected: PASS after reconciling the pruning expectations as noted in Step 3.

- [ ] **Step 5: Run the invariant audit to confirm the new module complies**

Run: `poetry run pytest tests/test_readonly_invariant.py -v`
Expected: PASS — `walker.py` performs no traversal of its own and no writes.

- [ ] **Step 6: Commit**

```bash
git add src/ftv/core/walker.py tests/test_walker.py
git commit -m "feat: walker turning a tree into classified file entries

Shared by the scan side and the card side so both apply an identical
notion of which files count. Junk directories are pruned during the walk,
and files that cannot be stat'ed become errors rather than vanishing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Index paths and the writable-path guard

**Files:**
- Create: `src/ftv/paths.py`
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing beyond stdlib and platformdirs
- Produces: `DbPathError`, `default_db_path() -> Path`, `validate_db_path(db_path, roots) -> None`. Task 6's `Index.open` calls `validate_db_path`.

This is the fourth enforcement layer from the spec: the index database must never land on a card or inside a destination.

- [ ] **Step 1: Write the failing test**

Create `tests/test_paths.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.paths'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/paths.py`:

```python
"""Where the index database may and may not live.

The index is the only thing this tool writes. Letting it land on a card or
inside a destination would violate the read-only invariant, so the location is
validated rather than trusted.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "ftv"

#: Mount points where removable media appears. macOS first; extend per platform.
REMOVABLE_PREFIXES: tuple[str, ...] = ("/Volumes", "/media", "/run/media", "/mnt")


class DbPathError(Exception):
    """Raised when the index database would be written somewhere unsafe."""


def default_db_path() -> Path:
    return Path(user_data_dir(APP_NAME)) / "index.db"


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_db_path(db_path: Path, roots: Iterable[Path]) -> None:
    """Raise :class:`DbPathError` unless ``db_path`` is a safe place to write.

    Rejects a database inside any scan root, and a database on a removable
    volume: either would mean writing to the very data the tool promises never
    to modify.
    """
    resolved = Path(db_path).expanduser().resolve()

    for prefix in REMOVABLE_PREFIXES:
        prefix_path = Path(prefix)
        if resolved != prefix_path and _is_within(resolved, prefix_path):
            raise DbPathError(
                f"refusing to write the index to {resolved}: that is a removable volume"
            )

    for root in roots:
        try:
            root_resolved = Path(root).expanduser().resolve()
        except OSError:
            continue
        if _is_within(resolved, root_resolved):
            raise DbPathError(
                f"refusing to write the index to {resolved}: that is inside a scan root "
                f"({root_resolved})"
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_paths.py -v && poetry run ruff check .`
Expected: all PASS, Ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/ftv/paths.py tests/test_paths.py
git commit -m "feat: index database path guard

The index is the only file the tool writes, so its location is validated
rather than trusted: never inside a scan root, never on a removable volume.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: SQLite index and the Lookup implementation

**Files:**
- Create: `src/ftv/core/index.py`
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `FileEntry`, `IndexedFile`, `Scan`, `Lookup` (Task 1); `validate_db_path`, `default_db_path` (Task 5)
- Produces: `SCHEMA_VERSION`, `ScanExistsError`, `ScanNotFoundError`, `ScanLookup` (implements `Lookup`), and `Index` with `open(db_path, roots=())`, `save_scan(name, roots, entries, use_mtime, skip_extensions, replace=False) -> Scan`, `get_scan(name) -> Scan | None`, `list_scans() -> list[Scan]`, `delete_scan(name) -> bool`, `lookup_for(name) -> ScanLookup`, `close()`. Tasks 9, 12 and 13 consume these.

`index.py` is the one module exempt from the write-surface audit, because it owns the database.

- [ ] **Step 1: Write the failing test**

Create `tests/test_index.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.core.index'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/core/index.py`:

```python
"""The remembered scans, stored in SQLite.

This is the one module allowed to write, and the only thing it writes is the
index database. It is exempt from the write-surface audit for that reason.

SQLite rather than a JSON file: a scan of a large destination is hundreds of
thousands of rows, a JSON index would be fully parsed on every run and fully
rewritten on every re-scan, and a crash mid-rewrite would leave a corrupt index
backing a "safe to format" decision.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from ftv.core.models import FileEntry, IndexedFile, Scan
from ftv.paths import default_db_path, validate_db_path

SCHEMA_VERSION = 1

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE scans (
            id              INTEGER PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            scanned_at      TEXT NOT NULL,
            use_mtime       INTEGER NOT NULL DEFAULT 1,
            skip_extensions TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE scan_roots (
            scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            path    TEXT NOT NULL
        );
        CREATE TABLE files (
            scan_id INTEGER NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
            path    TEXT NOT NULL,
            name    TEXT NOT NULL,
            size    INTEGER NOT NULL,
            mtime   REAL NOT NULL
        );
        CREATE INDEX files_lookup ON files (scan_id, name, size);
        CREATE INDEX scan_roots_scan ON scan_roots (scan_id);
        """,
    ),
)


class ScanExistsError(Exception):
    """Raised when saving over an existing scan without ``replace=True``."""


class ScanNotFoundError(Exception):
    """Raised when a named scan does not exist."""


class ScanLookup:
    """Answers name+size questions for one scan.

    Backed by the ``files_lookup`` index, so each question is one O(log n)
    probe rather than a scan of the table.
    """

    __slots__ = ("_conn", "_scan_id")

    def __init__(self, conn: sqlite3.Connection, scan_id: int) -> None:
        self._conn = conn
        self._scan_id = scan_id

    def find(self, name: str, size: int) -> list[IndexedFile]:
        rows = self._conn.execute(
            "SELECT path, name, size, mtime FROM files "
            "WHERE scan_id = ? AND name = ? AND size = ?",
            (self._scan_id, name, size),
        ).fetchall()
        return [
            IndexedFile(path=row["path"], name=row["name"], size=row["size"], mtime=row["mtime"])
            for row in rows
        ]


class Index:
    """Create, read and delete remembered scans."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    @classmethod
    def open(cls, db_path: Path | None = None, roots: Iterable[Path] = ()) -> Index:
        """Open (creating if needed) the index at ``db_path``.

        ``roots`` are validated against the database location so the index can
        never be written inside a destination or onto a card.
        """
        target = Path(db_path) if db_path is not None else default_db_path()
        validate_db_path(target, roots)
        target.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(target))

    def close(self) -> None:
        self._conn.close()

    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _migrate(self) -> None:
        current = self.schema_version()
        for version, script in _MIGRATIONS:
            if version > current:
                self._conn.executescript(script)
                self._conn.execute(f"PRAGMA user_version = {version}")
                self._conn.commit()

    def _scan_id(self, name: str) -> int:
        row = self._conn.execute("SELECT id FROM scans WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise ScanNotFoundError(f"no scan named {name!r}")
        return int(row["id"])

    def save_scan(
        self,
        name: str,
        roots: Sequence[Path],
        entries: Iterable[FileEntry],
        *,
        use_mtime: bool,
        skip_extensions: frozenset[str],
        replace: bool = False,
    ) -> Scan:
        """Store a scan and its files in one transaction."""
        existing = self._conn.execute(
            "SELECT id FROM scans WHERE name = ?", (name,)
        ).fetchone()
        if existing is not None and not replace:
            raise ScanExistsError(
                f"a scan named {name!r} already exists; use refresh or pass replace=True"
            )

        scanned_at = datetime.now(UTC)
        root_tuple = tuple(Path(r) for r in roots)

        with self._conn:
            if existing is not None:
                self._conn.execute("DELETE FROM scans WHERE id = ?", (int(existing["id"]),))
            cursor = self._conn.execute(
                "INSERT INTO scans (name, scanned_at, use_mtime, skip_extensions) "
                "VALUES (?, ?, ?, ?)",
                (
                    name,
                    scanned_at.isoformat(),
                    1 if use_mtime else 0,
                    json.dumps(sorted(skip_extensions)),
                ),
            )
            scan_id = int(cursor.lastrowid)
            self._conn.executemany(
                "INSERT INTO scan_roots (scan_id, path) VALUES (?, ?)",
                [(scan_id, str(root)) for root in root_tuple],
            )
            count = 0
            batch: list[tuple[int, str, str, int, float]] = []
            for entry in entries:
                batch.append((scan_id, str(entry.path), entry.name, entry.size, entry.mtime))
                count += 1
                if len(batch) >= 10_000:
                    self._conn.executemany(
                        "INSERT INTO files (scan_id, path, name, size, mtime) "
                        "VALUES (?, ?, ?, ?, ?)",
                        batch,
                    )
                    batch.clear()
            if batch:
                self._conn.executemany(
                    "INSERT INTO files (scan_id, path, name, size, mtime) VALUES (?, ?, ?, ?, ?)",
                    batch,
                )

        return Scan(
            name=name,
            roots=root_tuple,
            scanned_at=scanned_at,
            use_mtime=use_mtime,
            skip_extensions=skip_extensions,
            file_count=count,
        )

    def _row_to_scan(self, row: sqlite3.Row) -> Scan:
        scan_id = int(row["id"])
        roots = tuple(
            Path(r["path"])
            for r in self._conn.execute(
                "SELECT path FROM scan_roots WHERE scan_id = ? ORDER BY rowid", (scan_id,)
            )
        )
        count = int(
            self._conn.execute(
                "SELECT COUNT(*) AS n FROM files WHERE scan_id = ?", (scan_id,)
            ).fetchone()["n"]
        )
        return Scan(
            name=row["name"],
            roots=roots,
            scanned_at=datetime.fromisoformat(row["scanned_at"]),
            use_mtime=bool(row["use_mtime"]),
            skip_extensions=frozenset(json.loads(row["skip_extensions"])),
            file_count=count,
        )

    def get_scan(self, name: str) -> Scan | None:
        row = self._conn.execute(
            "SELECT id, name, scanned_at, use_mtime, skip_extensions FROM scans WHERE name = ?",
            (name,),
        ).fetchone()
        return None if row is None else self._row_to_scan(row)

    def list_scans(self) -> list[Scan]:
        rows = self._conn.execute(
            "SELECT id, name, scanned_at, use_mtime, skip_extensions FROM scans ORDER BY name"
        ).fetchall()
        return [self._row_to_scan(row) for row in rows]

    def delete_scan(self, name: str) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM scans WHERE name = ?", (name,))
        return cursor.rowcount > 0

    def lookup_for(self, name: str) -> ScanLookup:
        return ScanLookup(self._conn, self._scan_id(name))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_index.py -v`
Expected: all PASS.

- [ ] **Step 5: Confirm the audit still passes with index.py present**

Run: `poetry run pytest tests/test_readonly_invariant.py -v`
Expected: PASS. `index.py` is in `EXEMPT`; confirm no *other* core module gained a write call.

- [ ] **Step 6: Commit**

```bash
git add src/ftv/core/index.py tests/test_index.py
git commit -m "feat: SQLite scan index with name+size lookup

Path-agnostic lookup backed by a (scan_id, name, size) index, so a card
resolves in indexed probes rather than a walk of the destination. Lookup
returns every same-name/same-size candidate so the mtime signal has
something to choose from.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The pure comparison core

**Files:**
- Create: `src/ftv/core/compare.py`
- Test: `tests/test_compare.py`

**Interfaces:**
- Consumes: `FileEntry`, `IndexedFile`, `Bucket`, `Verdict`, `Lookup` (Task 1)
- Produces: `MTIME_TOLERANCE_SECONDS = 2.0`, `compare(entries, lookup, *, use_mtime=True, mtime_tolerance=MTIME_TOLERANCE_SECONDS) -> tuple[Verdict, ...]`, `timestamps_look_unpreserved(verdicts, *, minimum=10) -> bool`. Task 10 calls both.

This is the module whose correctness decides whether a card gets formatted. It touches no filesystem, no database and no terminal, so it is tested entirely against a fake `Lookup`.

**Why a tolerance:** FAT and exFAT — the filesystems SD cards use — store modification times at two-second granularity, so a byte-identical copy on an APFS destination can legitimately differ by up to two seconds. A zero-tolerance comparison would mark correctly copied files suspicious. `MTIME_TOLERANCE_SECONDS = 2.0` absorbs exactly that, while still separating files captured seconds apart from files captured months apart.

- [ ] **Step 1: Write the failing test**

Create `tests/test_compare.py`:

```python
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


def test_the_right_candidate_is_chosen_from_several_same_name_same_size_files():
    """April's DSC_0001.NEF and September's are both indexed; pick by capture time."""
    april = ssd_file(mtime=1_600_000_000.0, folder="april")
    september = ssd_file(mtime=1_700_000_000.0, folder="september")
    verdicts = compare([card_file(mtime=1_700_000_000.0)], FakeLookup([april, september]))

    assert verdicts[0].bucket is Bucket.PRESENT
    assert verdicts[0].matched is not None
    assert "september" in verdicts[0].matched.path


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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_compare.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.core.compare'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/core/compare.py`:

```python
"""The comparison core: card files plus a Lookup, in; verdicts, out.

Pure by design. No filesystem, no database, no terminal. This is the code whose
correctness decides whether a card gets formatted, so it is kept small enough
to read in one sitting and testable with a fake Lookup.
"""

from __future__ import annotations

from collections.abc import Iterable

from ftv.core.models import Bucket, FileEntry, IndexedFile, Lookup, Verdict

#: FAT and exFAT store modification times at two-second granularity, so a
#: byte-identical copy on an APFS destination can legitimately differ by up to
#: two seconds. Anything wider than this is a genuine signal, not filesystem
#: rounding.
MTIME_TOLERANCE_SECONDS = 2.0

#: Below this many matched files, "every match is suspicious" is more likely a
#: real collision than a copy tool that drops timestamps.
UNPRESERVED_MINIMUM = 10


def _best_by_mtime(
    entry: FileEntry, candidates: list[IndexedFile], tolerance: float
) -> IndexedFile | None:
    """Return the candidate whose mtime matches ``entry`` most closely, if any."""
    within = [c for c in candidates if abs(c.mtime - entry.mtime) <= tolerance]
    if not within:
        return None
    return min(within, key=lambda c: abs(c.mtime - entry.mtime))


def compare(
    entries: Iterable[FileEntry],
    lookup: Lookup,
    *,
    use_mtime: bool = True,
    mtime_tolerance: float = MTIME_TOLERANCE_SECONDS,
) -> tuple[Verdict, ...]:
    """Classify every card entry as present, suspicious, or missing.

    Matching is path-agnostic: basename plus byte size, so reorganising the
    destination never invalidates a scan. When ``use_mtime`` is set, a
    name-and-size match whose modification time also agrees is accepted; a
    match whose time disagrees is reported suspicious rather than passed,
    because two cards from the same camera hold different photographs under
    identical filenames.
    """
    verdicts: list[Verdict] = []

    for entry in entries:
        candidates = lookup.find(entry.name, entry.size)

        if not candidates:
            verdicts.append(Verdict(entry=entry, bucket=Bucket.MISSING))
            continue

        if not use_mtime:
            verdicts.append(
                Verdict(entry=entry, bucket=Bucket.PRESENT, matched=candidates[0])
            )
            continue

        best = _best_by_mtime(entry, candidates, mtime_tolerance)
        if best is not None:
            verdicts.append(Verdict(entry=entry, bucket=Bucket.PRESENT, matched=best))
            continue

        nearest = min(candidates, key=lambda c: abs(c.mtime - entry.mtime))
        verdicts.append(
            Verdict(
                entry=entry,
                bucket=Bucket.SUSPICIOUS,
                matched=nearest,
                reason="same name and size but a different capture time",
            )
        )

    return tuple(verdicts)


def timestamps_look_unpreserved(
    verdicts: Iterable[Verdict], *, minimum: int = UNPRESERVED_MINIMUM
) -> bool:
    """True when the results look like a copy tool that discarded timestamps.

    That case produces a wall of suspicious matches and no clean ones. Detecting
    it lets the report suggest turning the signal off for this scan instead of
    listing thousands of warnings.
    """
    matched = [v for v in verdicts if v.matched is not None]
    if len(matched) < minimum:
        return False
    return all(v.bucket is Bucket.SUSPICIOUS for v in matched)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_compare.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full suite and the audit**

Run: `poetry run pytest -v && poetry run ruff check .`
Expected: everything PASS; `compare.py` trips no audit rule (no filesystem access at all).

- [ ] **Step 6: Commit**

```bash
git add src/ftv/core/compare.py tests/test_compare.py
git commit -m "feat: pure comparison core with mtime as a confidence signal

Path-agnostic name+size matching. A same-name/same-size match whose
capture time disagrees is reported suspicious rather than passed, which
closes the two-cards-from-one-camera collision. Tolerance is 2s to absorb
exFAT timestamp granularity.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Confirm — the staleness guard

**Files:**
- Create: `src/ftv/core/confirm.py`
- Test: `tests/test_confirm.py`

**Interfaces:**
- Consumes: `fsread.stat_file` (Task 2); `Bucket`, `Verdict` (Task 1)
- Produces: `confirm(verdicts, *, stat_file=fsread.stat_file) -> tuple[Verdict, ...]`. Task 10 calls it after `compare`.

This closes the one dangerous direction of staleness. A scan taken in June still claims a file is present even if it was deleted from the destination in July, so every `PRESENT` verdict is re-checked against the destination's current state before the tool will call a card safe. It is one `stat` per matched file on a known path — not a re-walk.

- [ ] **Step 1: Write the failing test**

Create `tests/test_confirm.py`:

```python
from pathlib import Path

from ftv.core.confirm import confirm
from ftv.core.models import Bucket, FileEntry, IndexedFile, Verdict


def card_file(name: str = "A.JPG", size: int = 10) -> FileEntry:
    return FileEntry(
        path=Path("/Volumes/CARD/DCIM") / name,
        relpath=f"DCIM/{name}",
        name=name,
        size=size,
        mtime=1000.0,
    )


def indexed(name: str = "A.JPG", size: int = 10) -> IndexedFile:
    return IndexedFile(path=f"/Volumes/SSD/{name}", name=name, size=size, mtime=1000.0)


def present(name: str = "A.JPG", size: int = 10) -> Verdict:
    return Verdict(entry=card_file(name, size), bucket=Bucket.PRESENT, matched=indexed(name, size))


def test_a_file_still_on_the_destination_stays_present():
    def stat_file(path: Path) -> tuple[int, float]:
        return 10, 1000.0

    result = confirm((present(),), stat_file=stat_file)
    assert result[0].bucket is Bucket.PRESENT


def test_a_file_deleted_after_the_scan_becomes_suspicious():
    """The dangerous staleness direction: the index lies, the disk does not."""

    def stat_file(path: Path) -> tuple[int, float]:
        raise FileNotFoundError(2, "No such file or directory")

    result = confirm((present(),), stat_file=stat_file)
    assert result[0].bucket is Bucket.SUSPICIOUS
    assert "no longer" in result[0].reason.lower() or "not found" in result[0].reason.lower()
    assert result[0].matched is not None, "the report still needs to name the path it checked"


def test_a_file_whose_size_changed_after_the_scan_becomes_suspicious():
    def stat_file(path: Path) -> tuple[int, float]:
        return 4096, 1000.0

    result = confirm((present(),), stat_file=stat_file)
    assert result[0].bucket is Bucket.SUSPICIOUS
    assert "size" in result[0].reason.lower()


def test_an_unreadable_destination_file_becomes_suspicious():
    def stat_file(path: Path) -> tuple[int, float]:
        raise PermissionError(13, "Permission denied")

    result = confirm((present(),), stat_file=stat_file)
    assert result[0].bucket is Bucket.SUSPICIOUS


def test_missing_verdicts_are_passed_through_untouched():
    calls: list[Path] = []

    def stat_file(path: Path) -> tuple[int, float]:
        calls.append(path)
        return 10, 1000.0

    missing = Verdict(entry=card_file(), bucket=Bucket.MISSING)
    result = confirm((missing,), stat_file=stat_file)

    assert result == (missing,)
    assert calls == [], "a missing file has no destination path to confirm"


def test_already_suspicious_verdicts_are_passed_through_untouched():
    calls: list[Path] = []

    def stat_file(path: Path) -> tuple[int, float]:
        calls.append(path)
        return 10, 1000.0

    suspicious = Verdict(
        entry=card_file(), bucket=Bucket.SUSPICIOUS, matched=indexed(), reason="capture time"
    )
    result = confirm((suspicious,), stat_file=stat_file)

    assert result[0].reason == "capture time", "the original reason must survive"
    assert calls == []


def test_confirm_stats_the_matched_destination_path_not_the_card_path():
    seen: list[Path] = []

    def stat_file(path: Path) -> tuple[int, float]:
        seen.append(path)
        return 10, 1000.0

    confirm((present(),), stat_file=stat_file)
    assert seen == [Path("/Volumes/SSD/A.JPG")]


def test_confirm_checks_each_present_file_exactly_once():
    seen: list[Path] = []

    def stat_file(path: Path) -> tuple[int, float]:
        seen.append(path)
        return 10, 1000.0

    verdicts = tuple(present(name=f"IMG_{n}.JPG") for n in range(25))
    confirm(verdicts, stat_file=stat_file)
    assert len(seen) == 25


def test_confirm_against_the_real_filesystem(tmp_path: Path):
    real = tmp_path / "IMG.JPG"
    real.write_bytes(b"x" * 10)
    verdict = Verdict(
        entry=card_file("IMG.JPG", 10),
        bucket=Bucket.PRESENT,
        matched=IndexedFile(path=str(real), name="IMG.JPG", size=10, mtime=1000.0),
    )

    assert confirm((verdict,))[0].bucket is Bucket.PRESENT

    real.unlink()
    assert confirm((verdict,))[0].bucket is Bucket.SUSPICIOUS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_confirm.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.core.confirm'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/core/confirm.py`:

```python
"""Re-checks matched files against the destination's current state.

A remembered scan can go stale in two directions, and only one is dangerous:
files deleted from the destination after scanning, where the index still claims
they are present. This module closes that direction by stat-ing each matched
path before the tool will call a card safe.

This is not a re-scan. It is one stat per matched file on a path the index
already named, which is what keeps the "many cards in a row" workflow fast.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

from ftv.core import fsread
from ftv.core.models import Bucket, Verdict

StatFn = Callable[[Path], tuple[int, float]]


def confirm(
    verdicts: Iterable[Verdict],
    *,
    stat_file: StatFn = fsread.stat_file,
) -> tuple[Verdict, ...]:
    """Downgrade any PRESENT verdict whose destination file is no longer there.

    Only PRESENT verdicts are checked: a missing file has no destination path,
    and an already-suspicious verdict keeps its original reason rather than
    having it overwritten.
    """
    out: list[Verdict] = []

    for verdict in verdicts:
        if verdict.bucket is not Bucket.PRESENT or verdict.matched is None:
            out.append(verdict)
            continue

        try:
            size, _mtime = stat_file(Path(verdict.matched.path))
        except OSError as exc:
            out.append(
                replace(
                    verdict,
                    bucket=Bucket.SUSPICIOUS,
                    reason=f"no longer readable on the destination: {exc.strerror or exc}",
                )
            )
            continue

        if size != verdict.matched.size:
            out.append(
                replace(
                    verdict,
                    bucket=Bucket.SUSPICIOUS,
                    reason=(
                        f"size on the destination changed since the scan "
                        f"({verdict.matched.size} to {size} bytes)"
                    ),
                )
            )
            continue

        out.append(verdict)

    return tuple(out)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_confirm.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ftv/core/confirm.py tests/test_confirm.py
git commit -m "feat: staleness guard confirming matched files on disk

Every PRESENT verdict is re-stat'ed on the destination, so a green verdict
means the files are there now rather than that they were at scan time. One
stat per matched file on a known path, not a re-walk.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Deep hashing

**Files:**
- Create: `src/ftv/core/deephash.py`
- Test: `tests/test_deephash.py`

**Interfaces:**
- Consumes: `fsread.read_chunks` (Task 2); `Bucket`, `Verdict` (Task 1)
- Produces: `DeepScope` enum (`NONE`, `SUSPICIOUS`, `ALL`), `hash_file(path, *, chunk_size=1048576) -> str`, `resolve(verdicts, scope, *, hasher=hash_file) -> tuple[Verdict, ...]`. Task 10 and the CLI (Task 14) use `DeepScope`.

`--deep` hashes only the suspicious pairs, which is the targeted case: it resolves exactly the ambiguity the mtime signal flagged, usually across a handful of files. `--deep=all` hashes every matched pair and takes minutes per card.

- [ ] **Step 1: Write the failing test**

Create `tests/test_deephash.py`:

```python
from pathlib import Path

import pytest

from ftv.core.deephash import DeepScope, hash_file, resolve
from ftv.core.models import Bucket, FileEntry, IndexedFile, Verdict


def test_hash_file_is_stable_and_content_dependent(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    c = tmp_path / "c.bin"
    a.write_bytes(b"hello world")
    b.write_bytes(b"hello world")
    c.write_bytes(b"hello worlD")

    assert hash_file(a) == hash_file(b)
    assert hash_file(a) != hash_file(c)


def test_hash_file_streams_rather_than_loading_whole_files(tmp_path: Path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 100_000)
    assert hash_file(big, chunk_size=1024) == hash_file(big, chunk_size=65536)


def pair(tmp_path: Path, name: str, card_bytes: bytes, ssd_bytes: bytes) -> Verdict:
    card_dir = tmp_path / "card"
    ssd_dir = tmp_path / "ssd"
    card_dir.mkdir(exist_ok=True)
    ssd_dir.mkdir(exist_ok=True)
    card_path = card_dir / name
    ssd_path = ssd_dir / name
    card_path.write_bytes(card_bytes)
    ssd_path.write_bytes(ssd_bytes)
    return Verdict(
        entry=FileEntry(
            path=card_path, relpath=f"DCIM/{name}", name=name,
            size=len(card_bytes), mtime=1000.0,
        ),
        bucket=Bucket.SUSPICIOUS,
        matched=IndexedFile(path=str(ssd_path), name=name, size=len(ssd_bytes), mtime=2000.0),
        reason="same name and size but a different capture time",
    )


def test_identical_content_resolves_a_suspicious_file_to_present(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"same", b"same")
    result = resolve((verdict,), DeepScope.SUSPICIOUS)

    assert result[0].bucket is Bucket.PRESENT
    assert "content" in result[0].reason.lower()


def test_different_content_resolves_a_suspicious_file_to_missing(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"aaaa", b"bbbb")
    result = resolve((verdict,), DeepScope.SUSPICIOUS)

    assert result[0].bucket is Bucket.MISSING
    assert "content" in result[0].reason.lower()


def test_scope_none_changes_nothing(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"same", b"same")
    assert resolve((verdict,), DeepScope.NONE) == (verdict,)


def test_scope_suspicious_leaves_present_files_alone(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"aaaa", b"bbbb")
    already_present = Verdict(
        entry=verdict.entry, bucket=Bucket.PRESENT, matched=verdict.matched
    )
    result = resolve((already_present,), DeepScope.SUSPICIOUS)
    assert result[0].bucket is Bucket.PRESENT


def test_scope_all_downgrades_a_present_file_whose_content_differs(tmp_path: Path):
    """The exact-size collision that name+size cannot see."""
    verdict = pair(tmp_path, "A.JPG", b"aaaa", b"bbbb")
    claimed_present = Verdict(
        entry=verdict.entry, bucket=Bucket.PRESENT, matched=verdict.matched
    )
    result = resolve((claimed_present,), DeepScope.ALL)

    assert result[0].bucket is Bucket.MISSING


def test_scope_all_keeps_a_genuinely_identical_present_file(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"same", b"same")
    claimed_present = Verdict(
        entry=verdict.entry, bucket=Bucket.PRESENT, matched=verdict.matched
    )
    assert resolve((claimed_present,), DeepScope.ALL)[0].bucket is Bucket.PRESENT


def test_missing_files_are_never_hashed(tmp_path: Path):
    calls: list[Path] = []

    def hasher(path: Path, **_: object) -> str:
        calls.append(path)
        return "x"

    missing = Verdict(
        entry=FileEntry(path=tmp_path / "gone.JPG", relpath="gone.JPG", name="gone.JPG",
                        size=1, mtime=1.0),
        bucket=Bucket.MISSING,
    )
    result = resolve((missing,), DeepScope.ALL, hasher=hasher)

    assert result == (missing,)
    assert calls == [], "a missing file has nothing to compare against"


def test_an_unreadable_file_stays_suspicious_rather_than_passing(tmp_path: Path):
    verdict = pair(tmp_path, "A.JPG", b"same", b"same")

    def hasher(path: Path, **_: object) -> str:
        raise PermissionError(13, "Permission denied")

    result = resolve((verdict,), DeepScope.SUSPICIOUS, hasher=hasher)
    assert result[0].bucket is Bucket.SUSPICIOUS
    assert "could not" in result[0].reason.lower() or "denied" in result[0].reason.lower()


def test_deep_scope_parses_the_cli_spellings():
    assert DeepScope.from_flag(None) is DeepScope.NONE
    assert DeepScope.from_flag("") is DeepScope.SUSPICIOUS
    assert DeepScope.from_flag("suspicious") is DeepScope.SUSPICIOUS
    assert DeepScope.from_flag("all") is DeepScope.ALL
    assert DeepScope.from_flag("ALL") is DeepScope.ALL
    with pytest.raises(ValueError, match="deep"):
        DeepScope.from_flag("sometimes")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_deephash.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.core.deephash'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/core/deephash.py`:

```python
"""Optional content verification for matched pairs.

Name-and-size matching cannot see a same-size collision, and the mtime signal
only flags it. Hashing settles it. The default scope is the suspicious pairs
alone, because that is where the ambiguity is and it is usually a handful of
files; hashing every match reads both copies of everything and costs minutes
per card.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import replace
from enum import Enum
from pathlib import Path

from ftv.core import fsread
from ftv.core.models import Bucket, Verdict

DEFAULT_CHUNK_SIZE = 1 << 20

HashFn = Callable[..., str]


class DeepScope(Enum):
    """Which matched pairs to content-verify."""

    NONE = "none"
    SUSPICIOUS = "suspicious"
    ALL = "all"

    @classmethod
    def from_flag(cls, value: str | None) -> DeepScope:
        """Parse the CLI spellings: absent, bare ``--deep``, or ``--deep=all``."""
        if value is None:
            return cls.NONE
        normalised = value.strip().lower()
        if normalised in ("", "suspicious"):
            return cls.SUSPICIOUS
        if normalised == "all":
            return cls.ALL
        raise ValueError(f"unknown --deep value {value!r}; use 'suspicious' or 'all'")


def hash_file(path: Path, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Streamed blake2b digest of ``path``."""
    digest = hashlib.blake2b(digest_size=32)
    for chunk in fsread.read_chunks(path, chunk_size=chunk_size):
        digest.update(chunk)
    return digest.hexdigest()


def _targets(scope: DeepScope) -> frozenset[Bucket]:
    if scope is DeepScope.SUSPICIOUS:
        return frozenset({Bucket.SUSPICIOUS})
    return frozenset({Bucket.SUSPICIOUS, Bucket.PRESENT})


def resolve(
    verdicts: Iterable[Verdict],
    scope: DeepScope,
    *,
    hasher: HashFn = hash_file,
) -> tuple[Verdict, ...]:
    """Content-verify the matched pairs in ``scope`` and re-bucket them.

    A pair whose contents agree becomes PRESENT; one whose contents differ
    becomes MISSING, because the destination holds a different file under that
    name. A pair that cannot be read stays SUSPICIOUS: an unverifiable file
    must never be promoted to safe.
    """
    if scope is DeepScope.NONE:
        return tuple(verdicts)

    targets = _targets(scope)
    out: list[Verdict] = []

    for verdict in verdicts:
        if verdict.matched is None or verdict.bucket not in targets:
            out.append(verdict)
            continue

        try:
            card_digest = hasher(verdict.entry.path)
            ssd_digest = hasher(Path(verdict.matched.path))
        except OSError as exc:
            out.append(
                replace(
                    verdict,
                    bucket=Bucket.SUSPICIOUS,
                    reason=f"could not hash both copies: {exc.strerror or exc}",
                )
            )
            continue

        if card_digest == ssd_digest:
            out.append(
                replace(verdict, bucket=Bucket.PRESENT, reason="content verified identical")
            )
        else:
            out.append(
                replace(
                    verdict,
                    bucket=Bucket.MISSING,
                    reason="content differs from the file of that name on the destination",
                )
            )

    return tuple(out)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_deephash.py -v && poetry run pytest tests/test_readonly_invariant.py -v`
Expected: all PASS. `deephash.py` reads through `fsread.read_chunks` only, so the audit stays green.

- [ ] **Step 5: Commit**

```bash
git add src/ftv/core/deephash.py tests/test_deephash.py
git commit -m "feat: optional deep content verification

--deep hashes the suspicious pairs, resolving exactly the ambiguity the
mtime signal flagged; --deep=all hashes every match. A pair that cannot be
read stays suspicious rather than being promoted to safe.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Verify orchestrator

**Files:**
- Create: `src/ftv/core/verify.py`
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: `walk_tree`, `WalkOutcome` (Task 4); `compare`, `timestamps_look_unpreserved` (Task 7); `confirm` (Task 8); `DeepScope`, `resolve` (Task 9); `Scan`, `VerifyReport`, `Lookup` (Task 1)
- Produces: `DestinationUnavailableError`, `check_destination_available(scan) -> None`, `verify_card(card_root, scan, lookup, *, deep=DeepScope.NONE, skip_override=None) -> VerifyReport`, `suggests_disabling_mtime(report) -> bool`. Tasks 13 and 14 call these.

This is the single sequence that turns a mounted card into a report: walk → compare → confirm → optional deep resolve. Keeping it separate from `session.py` means the pipeline can be tested without any terminal involvement.

- [ ] **Step 1: Write the failing test**

Create `tests/test_verify.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ftv.core.deephash import DeepScope
from ftv.core.models import Bucket, IndexedFile, Scan
from ftv.core.verify import (
    DestinationUnavailableError,
    check_destination_available,
    suggests_disabling_mtime,
    verify_card,
)


class FakeLookup:
    def __init__(self, files: list[IndexedFile] | None = None) -> None:
        self.files = files or []

    def find(self, name: str, size: int) -> list[IndexedFile]:
        return [f for f in self.files if f.name == name and f.size == size]


def make_scan(root: Path, *, use_mtime: bool = True, skips: frozenset[str] = frozenset()) -> Scan:
    return Scan(
        name="Video SSD",
        roots=(root,),
        scanned_at=datetime(2026, 9, 14, tzinfo=UTC),
        use_mtime=use_mtime,
        skip_extensions=skips,
    )


@pytest.fixture
def card(tmp_path: Path) -> Path:
    root = tmp_path / "card"
    (root / "DCIM" / "100NZ_8").mkdir(parents=True)
    (root / "DCIM" / "100NZ_8" / "DSC_0001.JPG").write_bytes(b"a" * 10)
    (root / "DCIM" / "100NZ_8" / "DSC_0002.JPG").write_bytes(b"b" * 20)
    (root / "DCIM" / "100NZ_8" / "DSC_0003.NEF").write_bytes(b"c" * 30)
    (root / ".DS_Store").write_bytes(b"junk")
    return root


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    dest = tmp_path / "ssd"
    dest.mkdir()
    return dest


def copy_into(destination: Path, card: Path, relpath: str) -> IndexedFile:
    """Mirror a card file into the destination under a reorganised name."""
    source = card / relpath
    target = destination / "2026" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    stat = source.stat()
    import os

    os.utime(target, (stat.st_atime, stat.st_mtime))
    return IndexedFile(path=str(target), name=source.name, size=stat.st_size, mtime=stat.st_mtime)


def test_a_fully_transferred_card_is_safe(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0003.NEF"),
    ]
    report = verify_card(card, make_scan(destination), FakeLookup(indexed))

    assert report.safe_to_format is True
    assert report.exit_code == 0
    assert len(report.present) == 3
    assert report.skipped_junk == (".DS_Store",)


def test_a_missing_file_blocks_the_verdict(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
    ]
    report = verify_card(card, make_scan(destination), FakeLookup(indexed))

    assert report.safe_to_format is False
    assert report.exit_code == 1
    assert [v.entry.name for v in report.missing] == ["DSC_0003.NEF"]


def test_a_skip_listed_extension_is_not_required(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
    ]
    scan = make_scan(destination, skips=frozenset({".nef"}))
    report = verify_card(card, scan, FakeLookup(indexed))

    assert report.safe_to_format is True
    assert report.skipped_user == ("DCIM/100NZ_8/DSC_0003.NEF",)


def test_a_per_run_skip_override_replaces_the_scans_skip_list(card: Path, destination: Path):
    indexed = [copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG")]
    scan = make_scan(destination, skips=frozenset({".nef"}))
    report = verify_card(
        card, scan, FakeLookup(indexed), skip_override=frozenset({".nef", ".jpg"})
    )

    assert report.safe_to_format is True
    assert len(report.skipped_user) == 3


def test_the_confirm_step_catches_a_file_deleted_after_the_scan(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0003.NEF"),
    ]
    Path(indexed[2].path).unlink()

    report = verify_card(card, make_scan(destination), FakeLookup(indexed))

    assert report.safe_to_format is False
    assert [v.entry.name for v in report.suspicious] == ["DSC_0003.NEF"]


def test_deep_all_catches_a_same_size_different_content_collision(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0003.NEF"),
    ]
    # Same name, same size, same mtime, different bytes: invisible without hashing.
    Path(indexed[0].path).write_bytes(b"z" * 10)
    import os

    os.utime(indexed[0].path, (indexed[0].mtime, indexed[0].mtime))

    shallow = verify_card(card, make_scan(destination), FakeLookup(indexed))
    assert shallow.safe_to_format is True, "name+size+mtime cannot see this"

    deep = verify_card(card, make_scan(destination), FakeLookup(indexed), deep=DeepScope.ALL)
    assert deep.safe_to_format is False
    assert [v.entry.name for v in deep.missing] == ["DSC_0001.JPG"]


def test_an_empty_card_is_reported_as_nothing_to_verify(tmp_path: Path, destination: Path):
    empty = tmp_path / "empty-card"
    empty.mkdir()
    report = verify_card(empty, make_scan(destination), FakeLookup([]))

    assert report.checked_count == 0
    assert report.total_files == 0
    assert report.safe_to_format is True, "an empty card has nothing unresolved"


def test_a_card_that_cannot_be_read_yields_an_error_not_a_green_verdict(
    tmp_path: Path, destination: Path
):
    report = verify_card(tmp_path / "unplugged", make_scan(destination), FakeLookup([]))

    assert len(report.errors) == 1
    assert report.safe_to_format is False


def test_the_report_records_the_scan_name_and_card_root(card: Path, destination: Path):
    report = verify_card(card, make_scan(destination), FakeLookup([]))
    assert report.scan_name == "Video SSD"
    assert report.card_root == card


def test_verifying_refuses_when_a_destination_root_is_not_mounted(tmp_path: Path):
    scan = make_scan(tmp_path / "unplugged-ssd")
    with pytest.raises(DestinationUnavailableError, match="unplugged-ssd"):
        check_destination_available(scan)


def test_destination_check_passes_when_every_root_is_present(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    scan = Scan(name="S", roots=(a, b), scanned_at=datetime(2026, 9, 14, tzinfo=UTC))
    check_destination_available(scan)


def test_destination_check_names_every_missing_root(tmp_path: Path):
    present_root = tmp_path / "here"
    present_root.mkdir()
    scan = Scan(
        name="S",
        roots=(present_root, tmp_path / "gone-one", tmp_path / "gone-two"),
        scanned_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    with pytest.raises(DestinationUnavailableError) as excinfo:
        check_destination_available(scan)
    assert "gone-one" in str(excinfo.value)
    assert "gone-two" in str(excinfo.value)


def test_a_wall_of_suspicious_matches_suggests_disabling_the_mtime_signal(
    tmp_path: Path, destination: Path
):
    card = tmp_path / "card"
    card.mkdir()
    indexed = []
    for n in range(15):
        source = card / f"IMG_{n:04}.JPG"
        source.write_bytes(bytes([n]) * (100 + n))
        target = destination / f"IMG_{n:04}.JPG"
        target.write_bytes(source.read_bytes())
        indexed.append(
            IndexedFile(
                path=str(target), name=target.name, size=100 + n, mtime=1.0
            )
        )

    report = verify_card(card, make_scan(destination), FakeLookup(indexed))
    assert len(report.suspicious) == 15
    assert suggests_disabling_mtime(report) is True


def test_no_suggestion_when_matches_are_clean(card: Path, destination: Path):
    indexed = [
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0001.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0002.JPG"),
        copy_into(destination, card, "DCIM/100NZ_8/DSC_0003.NEF"),
    ]
    report = verify_card(card, make_scan(destination), FakeLookup(indexed))
    assert suggests_disabling_mtime(report) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.core.verify'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/core/verify.py`:

```python
"""Turns a mounted card into a VerifyReport.

One sequence: walk the card, compare against the scan, confirm the matches are
still on the destination, then optionally hash. Kept separate from the terminal
session so the whole pipeline is testable without any UI.
"""

from __future__ import annotations

from pathlib import Path

from ftv.core.compare import compare, timestamps_look_unpreserved
from ftv.core.confirm import confirm
from ftv.core.deephash import DeepScope, resolve
from ftv.core.models import Lookup, Scan, VerifyReport
from ftv.core.walker import walk_tree


class DestinationUnavailableError(Exception):
    """Raised when a scan's destination roots are not currently mounted."""


def check_destination_available(scan: Scan) -> None:
    """Raise unless every root of ``scan`` is present.

    Without this, an unplugged destination would fail every confirmation and
    turn a perfectly good card into a wall of suspicious files. Refusing is the
    honest answer: the tool cannot verify anything it cannot reach.
    """
    absent = [str(root) for root in scan.roots if not root.is_dir()]
    if absent:
        raise DestinationUnavailableError(
            "these destination roots are not available: " + ", ".join(absent)
        )


def verify_card(
    card_root: Path,
    scan: Scan,
    lookup: Lookup,
    *,
    deep: DeepScope = DeepScope.NONE,
    skip_override: frozenset[str] | None = None,
) -> VerifyReport:
    """Verify one card against one remembered scan."""
    skips = scan.skip_extensions if skip_override is None else skip_override

    outcome = walk_tree(card_root, skips)
    verdicts = compare(outcome.entries, lookup, use_mtime=scan.use_mtime)
    verdicts = confirm(verdicts)
    verdicts = resolve(verdicts, deep)

    return VerifyReport(
        scan_name=scan.name,
        card_root=card_root,
        verdicts=verdicts,
        skipped_user=outcome.skipped_user,
        skipped_junk=outcome.skipped_junk,
        errors=outcome.errors,
    )


def suggests_disabling_mtime(report: VerifyReport) -> bool:
    """True when the report looks like a copy tool that discarded timestamps."""
    return timestamps_look_unpreserved(report.verdicts)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_verify.py -v`
Expected: all PASS.

Note: `test_deep_all_catches_a_same_size_different_content_collision` and the `copy_into` helper call `os.utime` and `write_bytes` — that is test code building fixtures, not `core` code, so the audit does not apply.

- [ ] **Step 5: Commit**

```bash
git add src/ftv/core/verify.py tests/test_verify.py
git commit -m "feat: verify orchestrator

walk, compare, confirm, optional deep resolve. Refuses to verify when a
destination root is not mounted, because every confirmation would fail and
turn a good card into a wall of warnings.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Removable volume detection

**Files:**
- Create: `src/ftv/volumes.py`
- Test: `tests/test_volumes.py`

**Interfaces:**
- Consumes: nothing beyond stdlib
- Produces: `Volume(path, name, total_bytes)`, `VolumeDetector` protocol with `list_removable() -> list[Volume]`, `MacOSVolumeDetector`, `FakeVolumeDetector`, and `wait_for_new_volume(detector, *, seen: set[Path], poll_interval=1.0, sleeper=time.sleep, stop_after=None) -> Volume | None` (which mutates `seen`, pruning ejected volumes). Task 14's session loop consumes all of these.

**Two deviations from the spec, both deliberate:**

1. **This module lives at `src/ftv/volumes.py`, not `src/ftv/core/volumes.py`.** Enumerating mount points means listing a directory, and the audit test from Task 2 forbids directory traversal anywhere in `core` outside `fsread`. Rather than weaken that rule, mount discovery moves out of `core` — it inspects the system's mount table, not the user's files, so it does not belong behind the read-only data-access boundary anyway.
2. **The protocol has one method, `list_removable()`, rather than the spec's `list_removable()` plus `watch()`.** Watching is built on top as a module-level function, which keeps the protocol trivial to fake and puts the polling loop under test without a second fake implementation.

`FakeVolumeDetector` ships in the production module rather than the test file because Task 14's session tests need it too, and a scripted detector is the only way to test the card loop deterministically.

- [ ] **Step 1: Write the failing test**

Create `tests/test_volumes.py`:

```python
import os
import sys
from pathlib import Path

import pytest

from ftv.volumes import (
    FakeVolumeDetector,
    MacOSVolumeDetector,
    Volume,
    VolumeDetector,
    wait_for_new_volume,
)


def test_fake_detector_satisfies_the_protocol():
    detector = FakeVolumeDetector([[]])
    assert isinstance(detector, VolumeDetector)


def test_fake_detector_replays_scripted_states():
    card = Volume(path=Path("/Volumes/NIKON Z8"), name="NIKON Z8", total_bytes=1000)
    detector = FakeVolumeDetector([[], [card], []])

    assert detector.list_removable() == []
    assert detector.list_removable() == [card]
    assert detector.list_removable() == []


def test_fake_detector_repeats_its_final_state_forever():
    card = Volume(path=Path("/Volumes/CARD"), name="CARD", total_bytes=1)
    detector = FakeVolumeDetector([[card]])
    assert detector.list_removable() == [card]
    assert detector.list_removable() == [card]


def test_wait_returns_a_volume_that_was_not_already_seen():
    card = Volume(path=Path("/Volumes/CARD"), name="CARD", total_bytes=1)
    detector = FakeVolumeDetector([[], [], [card]])
    slept: list[float] = []

    found = wait_for_new_volume(detector, seen=set(), poll_interval=0.5, sleeper=slept.append)

    assert found == card
    assert slept == [0.5, 0.5], "polls until something new appears"


def test_wait_ignores_a_volume_that_was_already_mounted():
    already = Volume(path=Path("/Volumes/TIME MACHINE"), name="TIME MACHINE", total_bytes=1)
    card = Volume(path=Path("/Volumes/CARD"), name="CARD", total_bytes=1)
    detector = FakeVolumeDetector([[already], [already, card]])

    found = wait_for_new_volume(
        detector, seen={already.path}, poll_interval=0, sleeper=lambda _: None
    )
    assert found == card


def test_wait_forgets_a_volume_that_was_ejected():
    """Re-inserting a card must offer it again."""
    card = Volume(path=Path("/Volumes/CARD"), name="CARD", total_bytes=1)
    detector = FakeVolumeDetector([[], [card]])
    seen = {card.path}

    found = wait_for_new_volume(detector, seen=seen, poll_interval=0, sleeper=lambda _: None)

    assert found == card
    assert seen == set(), "the ejected volume was pruned from the caller's set"


def test_wait_gives_up_after_the_configured_number_of_polls():
    detector = FakeVolumeDetector([[]])
    found = wait_for_new_volume(
        detector, seen=set(), poll_interval=0, sleeper=lambda _: None, stop_after=3
    )
    assert found is None


def test_wait_polls_the_detector_once_per_cycle():
    """Extra polls desynchronise a scripted fake and a real mount table alike."""
    detector = FakeVolumeDetector([[]])
    wait_for_new_volume(
        detector, seen=set(), poll_interval=0, sleeper=lambda _: None, stop_after=3
    )
    assert detector.calls == 3


def test_wait_returns_the_first_new_volume_when_several_appear_at_once():
    a = Volume(path=Path("/Volumes/A"), name="A", total_bytes=1)
    b = Volume(path=Path("/Volumes/B"), name="B", total_bytes=1)
    detector = FakeVolumeDetector([[b, a]])

    found = wait_for_new_volume(detector, seen=set(), poll_interval=0, sleeper=lambda _: None)
    assert found in (a, b)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS mount layout")
def test_macos_detector_returns_volumes_without_raising():
    volumes = MacOSVolumeDetector().list_removable()
    assert isinstance(volumes, list)
    assert all(isinstance(v, Volume) for v in volumes)
    assert all(v.path.is_dir() for v in volumes)


def test_macos_detector_excludes_the_boot_volume(tmp_path: Path, monkeypatch):
    """The boot volume shares its device with /, so it must never be offered."""
    fake_volumes = tmp_path / "Volumes"
    fake_volumes.mkdir()
    (fake_volumes / "Macintosh HD").mkdir()

    detector = MacOSVolumeDetector(mount_root=fake_volumes)
    root_dev = os.stat(os.sep).st_dev
    monkeypatch.setattr(
        "ftv.volumes.os.stat",
        lambda path, *a, **k: type("S", (), {"st_dev": root_dev})(),
    )

    assert detector.list_removable() == []


def test_macos_detector_returns_an_empty_list_when_the_mount_root_is_absent(tmp_path: Path):
    detector = MacOSVolumeDetector(mount_root=tmp_path / "no-such-dir")
    assert detector.list_removable() == []


def test_macos_detector_skips_symlinked_mount_entries(tmp_path: Path):
    fake_volumes = tmp_path / "Volumes"
    fake_volumes.mkdir()
    real = tmp_path / "elsewhere"
    real.mkdir()
    (fake_volumes / "Macintosh HD").symlink_to(real)

    detector = MacOSVolumeDetector(mount_root=fake_volumes)
    assert [v.name for v in detector.list_removable()] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_volumes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.volumes'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/volumes.py`:

```python
"""Finding mounted volumes that might be a card.

This inspects the system's mount points rather than the user's files, so it
sits outside ``ftv.core`` and outside the read-only data-access boundary.

The tool cannot reliably tell an SD card from an external SSD without shelling
out to platform tools, and it does not need to: every card is confirmed with
the user before being verified, and verification is read-only, so the cost of
offering the wrong drive is a wasted scan rather than any risk to data.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

DEFAULT_POLL_INTERVAL = 1.0


@dataclass(frozen=True, slots=True)
class Volume:
    """A mounted volume that could hold card files."""

    path: Path
    name: str
    total_bytes: int


@runtime_checkable
class VolumeDetector(Protocol):
    """Lists volumes that are candidates for being a card."""

    def list_removable(self) -> list[Volume]: ...


class MacOSVolumeDetector:
    """Lists non-boot volumes mounted under ``/Volumes``.

    The boot volume is excluded by comparing device ids against ``/``, which is
    more robust than matching its name. Symlinked entries are skipped: macOS
    represents the boot volume that way.
    """

    __slots__ = ("_mount_root",)

    def __init__(self, mount_root: Path | str = "/Volumes") -> None:
        self._mount_root = Path(mount_root)

    def list_removable(self) -> list[Volume]:
        if not self._mount_root.is_dir():
            return []

        try:
            root_device = os.stat(os.sep).st_dev
        except OSError:
            root_device = -1

        volumes: list[Volume] = []
        try:
            names = sorted(os.listdir(self._mount_root))
        except OSError:
            return []

        for name in names:
            candidate = self._mount_root / name
            try:
                if candidate.is_symlink() or not candidate.is_dir():
                    continue
                if os.stat(candidate).st_dev == root_device:
                    continue
                usage = os.statvfs(candidate)
            except OSError:
                continue
            volumes.append(
                Volume(
                    path=candidate,
                    name=name,
                    total_bytes=usage.f_blocks * usage.f_frsize,
                )
            )
        return volumes


class FakeVolumeDetector:
    """Replays a scripted sequence of mount states.

    Lives in the production module because the session tests need it too: a
    scripted detector is the only way to drive the card loop deterministically.
    The final state repeats forever, so a loop cannot run off the end.
    """

    __slots__ = ("_states", "_calls")

    def __init__(self, states: Sequence[Sequence[Volume]]) -> None:
        if not states:
            raise ValueError("at least one state is required")
        self._states = [list(state) for state in states]
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    def list_removable(self) -> list[Volume]:
        index = min(self._calls, len(self._states) - 1)
        self._calls += 1
        return list(self._states[index])


def wait_for_new_volume(
    detector: VolumeDetector,
    *,
    seen: set[Path],
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    sleeper: Callable[[float], None] = time.sleep,
    stop_after: int | None = None,
) -> Volume | None:
    """Poll until a volume appears that is not in ``seen``.

    ``seen`` is MUTATED: volumes that have been ejected are removed from it, so
    re-inserting a card offers it again. The caller adds a volume to ``seen``
    once it has been handled. Doing the pruning here rather than in the caller
    means the detector is polled exactly once per cycle, which matters both for
    real mount tables and for scripted fakes in tests.

    ``sleeper`` is injected so the loop is testable without real time. Returns
    None when ``stop_after`` polls elapse with nothing new, which is what lets
    a caller offer a way out of the wait.
    """
    polls = 0

    while stop_after is None or polls < stop_after:
        current = detector.list_removable()
        seen &= {volume.path for volume in current}

        for volume in current:
            if volume.path not in seen:
                return volume

        polls += 1
        if stop_after is not None and polls >= stop_after:
            break
        sleeper(poll_interval)

    return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_volumes.py -v`
Expected: all PASS. On non-macOS machines the live-detector test skips.

- [ ] **Step 5: Confirm the audit is unaffected**

Run: `poetry run pytest tests/test_readonly_invariant.py -v`
Expected: PASS. `volumes.py` is outside `core`, so its `os.listdir` does not trip the traversal rule — which is exactly why it was moved.

- [ ] **Step 6: Commit**

```bash
git add src/ftv/volumes.py tests/test_volumes.py
git commit -m "feat: removable volume detection with an injectable poll loop

Lives outside core: it inspects mount points, not user files, so it stays
outside the read-only data-access boundary and the traversal audit rule
stays strict. The boot volume is excluded by device id rather than name.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Rendering

**Files:**
- Create: `src/ftv/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `VerifyReport`, `Scan`, `Bucket`, `Verdict` (Task 1); `Volume` (Task 11)
- Produces: `human_bytes(n) -> str`, `human_age(then, *, now=None) -> str`, `build_missing_tree(report) -> Tree`, `render_report(console, report, scan, *, suggest_no_mtime=False) -> None`, `render_scan_list(console, scans) -> None`, `render_card_header(console, scan, volume, file_count) -> None`, `report_to_dict(report) -> dict`. Tasks 13 and 14 call these.

This is the only module that prints. Tests assert on the text Rich produces into a `Console(record=True)`, which keeps the UI verifiable without a TUI harness.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render.py`:

```python
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rich.console import Console

from ftv.core.models import Bucket, FileEntry, IndexedFile, Scan, Verdict, VerifyReport, WalkError
from ftv.render import (
    build_missing_tree,
    human_age,
    human_bytes,
    render_report,
    render_scan_list,
    report_to_dict,
)


def console() -> Console:
    return Console(record=True, width=100, force_terminal=False, no_color=True)


def text(c: Console) -> str:
    return c.export_text()


def entry(name: str, relpath: str, size: int = 10) -> FileEntry:
    return FileEntry(path=Path("/Volumes/CARD") / relpath, relpath=relpath, name=name,
                     size=size, mtime=1000.0)


def scan(use_mtime: bool = True) -> Scan:
    return Scan(
        name="Video SSD",
        roots=(Path("/Volumes/SSD/Video"),),
        scanned_at=datetime.now(UTC) - timedelta(days=3),
        use_mtime=use_mtime,
        skip_extensions=frozenset({".nef"}),
        file_count=48213,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (1024, "1.0 KB"),
        (1_500_000, "1.4 MB"),
        (2_147_483_648, "2.0 GB"),
        (335_007_449_088, "312.0 GB"),
    ],
)
def test_human_bytes(value, expected):
    assert human_bytes(value) == expected


def test_human_age_reads_naturally():
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    assert human_age(now - timedelta(minutes=5), now=now) == "just now"
    assert human_age(now - timedelta(hours=3), now=now) == "3 hours ago"
    assert human_age(now - timedelta(days=1), now=now) == "1 day ago"
    assert human_age(now - timedelta(days=3), now=now) == "3 days ago"
    assert human_age(now - timedelta(days=45), now=now) == "45 days ago"


def test_a_clean_report_says_it_is_safe_to_format():
    report = VerifyReport(
        scan_name="Video SSD",
        card_root=Path("/Volumes/NIKON Z8"),
        verdicts=(Verdict(entry=entry("A.JPG", "DCIM/A.JPG"), bucket=Bucket.PRESENT),),
    )
    c = console()
    render_report(c, report, scan())
    out = text(c)

    assert "safe to format" in out.lower()
    assert "1" in out


def test_a_clean_report_reports_what_was_skipped():
    report = VerifyReport(
        scan_name="Video SSD",
        card_root=Path("/Volumes/NIKON Z8"),
        verdicts=(Verdict(entry=entry("A.JPG", "DCIM/A.JPG"), bucket=Bucket.PRESENT),),
        skipped_user=("DCIM/B.NEF", "DCIM/C.NEF"),
        skipped_junk=(".DS_Store",),
    )
    c = console()
    render_report(c, report, scan())
    out = text(c)

    assert "2" in out and "skip" in out.lower()
    assert "junk" in out.lower()


def test_a_report_with_missing_files_says_do_not_format():
    report = VerifyReport(
        scan_name="Video SSD",
        card_root=Path("/Volumes/NIKON Z8"),
        verdicts=(
            Verdict(entry=entry("DSC_0412.MOV", "DCIM/100NZ_8/DSC_0412.MOV", 2_100_000_000),
                    bucket=Bucket.MISSING),
        ),
    )
    c = console()
    render_report(c, report, scan())
    out = text(c)

    assert "not" in out.lower() and "format" in out.lower()
    assert "DSC_0412.MOV" in out


def test_missing_files_are_shown_with_the_folder_structure_leading_to_them():
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        verdicts=(
            Verdict(entry=entry("DSC_0412.MOV", "DCIM/100NZ_8/DSC_0412.MOV"), bucket=Bucket.MISSING),
            Verdict(entry=entry("DSC_0413.MOV", "DCIM/100NZ_8/DSC_0413.MOV"), bucket=Bucket.MISSING),
            Verdict(entry=entry("C0007.MP4", "PRIVATE/M4ROOT/CLIP/C0007.MP4"), bucket=Bucket.MISSING),
        ),
    )
    c = console()
    c.print(build_missing_tree(report))
    out = text(c)

    assert "DCIM" in out
    assert "100NZ_8" in out
    assert "PRIVATE" in out
    assert "M4ROOT" in out
    assert "DSC_0412.MOV" in out
    assert "C0007.MP4" in out


def test_the_missing_tree_groups_files_under_a_shared_folder_once():
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        verdicts=(
            Verdict(entry=entry("A.MOV", "DCIM/100NZ_8/A.MOV"), bucket=Bucket.MISSING),
            Verdict(entry=entry("B.MOV", "DCIM/100NZ_8/B.MOV"), bucket=Bucket.MISSING),
        ),
    )
    c = console()
    c.print(build_missing_tree(report))
    assert text(c).count("100NZ_8") == 1


def test_suspicious_files_are_shown_with_their_reason():
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        verdicts=(
            Verdict(
                entry=entry("DSC_0001.NEF", "DCIM/100NZ_8/DSC_0001.NEF"),
                bucket=Bucket.SUSPICIOUS,
                matched=IndexedFile(path="/Volumes/SSD/april/DSC_0001.NEF",
                                    name="DSC_0001.NEF", size=10, mtime=1.0),
                reason="same name and size but a different capture time",
            ),
        ),
    )
    c = console()
    render_report(c, report, scan())
    out = text(c)

    assert "suspicious" in out.lower()
    assert "DSC_0001.NEF" in out
    assert "capture time" in out
    assert "--deep" in out, "the user needs to be told how to settle it"


def test_errors_are_shown_and_block_the_verdict():
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        errors=(WalkError(path=Path("/Volumes/CARD/bad"), message="permission denied"),),
    )
    c = console()
    render_report(c, report, scan())
    out = text(c)

    assert "permission denied" in out
    assert "safe to format" not in out.lower()


def test_an_empty_card_is_not_reported_as_all_present():
    """An empty card and a card that failed to mount look identical by count."""
    report = VerifyReport(scan_name="S", card_root=Path("/Volumes/CARD"))
    c = console()
    render_report(c, report, scan())
    out = text(c).lower()

    assert "no files" in out
    assert "all" not in out or "present" not in out


def test_the_no_mtime_suggestion_is_shown_when_asked_for():
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        verdicts=tuple(
            Verdict(entry=entry(f"IMG_{n}.JPG", f"DCIM/IMG_{n}.JPG"), bucket=Bucket.SUSPICIOUS,
                    reason="same name and size but a different capture time")
            for n in range(12)
        ),
    )
    c = console()
    render_report(c, report, scan(), suggest_no_mtime=True)
    out = text(c)

    assert "--no-mtime" in out
    assert "timestamp" in out.lower()


def test_the_report_shows_the_scans_age():
    report = VerifyReport(scan_name="Video SSD", card_root=Path("/Volumes/CARD"))
    c = console()
    render_report(c, report, scan())
    assert "3 days ago" in text(c)


def test_scan_list_shows_names_roots_counts_and_age():
    c = console()
    render_scan_list(c, [scan()])
    out = text(c)

    assert "Video SSD" in out
    assert "48,213" in out
    assert "3 days ago" in out
    assert "/Volumes/SSD/Video" in out
    assert ".nef" in out


def test_scan_list_says_so_when_there_are_none():
    c = console()
    render_scan_list(c, [])
    assert "no scans" in text(c).lower()


def test_report_to_dict_is_json_serialisable_and_carries_the_verdict():
    report = VerifyReport(
        scan_name="Video SSD",
        card_root=Path("/Volumes/NIKON Z8"),
        verdicts=(
            Verdict(entry=entry("A.JPG", "DCIM/A.JPG"), bucket=Bucket.PRESENT),
            Verdict(entry=entry("B.MOV", "DCIM/B.MOV", 2_100_000_000), bucket=Bucket.MISSING),
        ),
        skipped_user=("DCIM/C.NEF",),
        skipped_junk=(".DS_Store",),
        errors=(WalkError(path=Path("/Volumes/NIKON Z8/bad"), message="io error"),),
    )
    payload = json.loads(json.dumps(report_to_dict(report)))

    assert payload["scan"] == "Video SSD"
    assert payload["card_root"] == "/Volumes/NIKON Z8"
    assert payload["safe_to_format"] is False
    assert payload["exit_code"] == 1
    assert payload["counts"]["present"] == 1
    assert payload["counts"]["missing"] == 1
    assert payload["counts"]["skipped_user"] == 1
    assert payload["counts"]["skipped_junk"] == 1
    assert payload["counts"]["error"] == 1
    assert payload["missing"] == [{"relpath": "DCIM/B.MOV", "size": 2_100_000_000}]
    assert payload["errors"] == [{"path": "/Volumes/NIKON Z8/bad", "message": "io error"}]


def test_report_to_dict_includes_suspicious_details():
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        verdicts=(
            Verdict(
                entry=entry("DSC_0001.NEF", "DCIM/DSC_0001.NEF"),
                bucket=Bucket.SUSPICIOUS,
                matched=IndexedFile(path="/Volumes/SSD/DSC_0001.NEF", name="DSC_0001.NEF",
                                    size=10, mtime=1.0),
                reason="same name and size but a different capture time",
            ),
        ),
    )
    payload = report_to_dict(report)

    assert payload["suspicious"] == [
        {
            "relpath": "DCIM/DSC_0001.NEF",
            "size": 10,
            "matched": "/Volumes/SSD/DSC_0001.NEF",
            "reason": "same name and size but a different capture time",
        }
    ]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.render'`

- [ ] **Step 3: Write the implementation**

Create `src/ftv/render.py`:

```python
"""Everything the tool prints.

Confining output here keeps the pipeline testable without a terminal, and keeps
the UI testable by asserting on recorded text rather than driving a TUI.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from ftv.core.models import Bucket, Scan, VerifyReport
from ftv.volumes import Volume

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_bytes(value: int) -> str:
    """Format a byte count for a person reading it at a glance."""
    size = float(value)
    for unit in _UNITS:
        if unit == "B":
            if size < 1024:
                return f"{int(size)} B"
        elif size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} EB"


def human_age(then: datetime, *, now: datetime | None = None) -> str:
    """Describe how long ago ``then`` was."""
    reference = now or datetime.now(UTC)
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    delta = reference - then
    seconds = int(delta.total_seconds())

    if seconds < 3600:
        return "just now"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def build_missing_tree(report: VerifyReport) -> Tree:
    """Render missing files under the card's own folder structure."""
    tree = Tree(str(report.card_root))
    branches: dict[str, Tree] = {}

    for verdict in sorted(report.missing, key=lambda v: v.entry.relpath):
        parts = PurePosixPath(verdict.entry.relpath).parts
        node = tree
        prefix = ""
        for folder in parts[:-1]:
            prefix = f"{prefix}/{folder}"
            if prefix not in branches:
                branches[prefix] = node.add(f"[bold]{folder}/[/bold]")
            node = branches[prefix]
        node.add(f"{parts[-1]}  [dim]{human_bytes(verdict.entry.size)}[/dim]")

    return tree


def render_card_header(
    console: Console, scan: Scan, volume: Volume, file_count: int | None = None
) -> None:
    counted = "" if file_count is None else f" · {file_count:,} files"
    console.print(
        f"[bold]Scan:[/bold] {scan.name}   "
        f"[dim]scanned {human_age(scan.scanned_at)} · {scan.file_count:,} files[/dim]"
    )
    console.print(
        f"[bold]Card:[/bold] {volume.name}   "
        f"[dim]{volume.path} · {human_bytes(volume.total_bytes)}{counted}[/dim]"
    )


def _render_skipped(console: Console, report: VerifyReport) -> None:
    pieces = []
    if report.skipped_user:
        pieces.append(f"{len(report.skipped_user):,} by your skip list")
    if report.skipped_junk:
        pieces.append(f"{len(report.skipped_junk):,} junk")
    if pieces:
        console.print(f"   [dim]skipped: {' · '.join(pieces)}[/dim]")


def render_report(
    console: Console,
    report: VerifyReport,
    scan: Scan,
    *,
    suggest_no_mtime: bool = False,
) -> None:
    """Print the verdict for one card."""
    console.print(
        f"[dim]scan {report.scan_name} · scanned {human_age(scan.scanned_at)}[/dim]"
    )

    if report.total_files == 0:
        console.print("   [yellow]no files to verify on this card[/yellow]")
        console.print(
            "   [dim]an empty card and a card that failed to mount look the same "
            "from a file count, so no verdict is given[/dim]"
        )
        return

    if report.safe_to_format:
        console.print(
            f"   [bold green]ALL {len(report.present):,} FILES PRESENT[/bold green]"
            " — safe to format"
        )
        _render_skipped(console, report)
        return

    if report.missing:
        console.print(
            f"   [bold red]{len(report.missing):,} FILE"
            f"{'S' if len(report.missing) != 1 else ''} MISSING[/bold red]"
            " — do NOT format"
        )
        console.print(build_missing_tree(report))

    if report.suspicious:
        console.print(
            f"   [bold yellow]{len(report.suspicious):,} SUSPICIOUS[/bold yellow]"
            " — matched by name and size, but not confirmed"
        )
        for verdict in sorted(report.suspicious, key=lambda v: v.entry.relpath):
            console.print(f"     {verdict.entry.relpath}  [dim]{verdict.reason}[/dim]")
            if verdict.matched is not None:
                console.print(f"       [dim]destination: {verdict.matched.path}[/dim]")
        console.print("   [dim]run again with --deep to settle these by content[/dim]")

    if report.errors:
        console.print(
            f"   [bold red]{len(report.errors):,} UNREADABLE[/bold red]"
            " — these could not be checked"
        )
        for error in report.errors:
            console.print(f"     {error.path}  [dim]{error.message}[/dim]")

    _render_skipped(console, report)

    if suggest_no_mtime:
        console.print(
            "   [yellow]Every match disagreed on timestamps.[/yellow] That usually means the "
            "copy tool did not preserve them rather than that the files differ. Re-scan this "
            "destination with [bold]--no-mtime[/bold] to turn the signal off."
        )


def render_scan_list(console: Console, scans: Sequence[Scan]) -> None:
    if not scans:
        console.print("[yellow]no scans yet[/yellow] — add one with [bold]ftv scan add[/bold]")
        return

    table = Table(box=None, pad_edge=False)
    table.add_column("Scan", style="bold")
    table.add_column("Files", justify="right")
    table.add_column("Scanned")
    table.add_column("mtime")
    table.add_column("Skipped")
    table.add_column("Roots")

    for scan in scans:
        table.add_row(
            scan.name,
            f"{scan.file_count:,}",
            human_age(scan.scanned_at),
            "on" if scan.use_mtime else "off",
            ", ".join(sorted(scan.skip_extensions)) or "—",
            "\n".join(str(root) for root in scan.roots),
        )
    console.print(table)


def report_to_dict(report: VerifyReport) -> dict:
    """A JSON-serialisable form of the report, for --json."""
    return {
        "scan": report.scan_name,
        "card_root": str(report.card_root),
        "safe_to_format": report.safe_to_format,
        "exit_code": report.exit_code,
        "counts": {bucket.value: count for bucket, count in report.counts().items()},
        "missing": [
            {"relpath": v.entry.relpath, "size": v.entry.size}
            for v in sorted(report.missing, key=lambda v: v.entry.relpath)
        ],
        "suspicious": [
            {
                "relpath": v.entry.relpath,
                "size": v.entry.size,
                "matched": v.matched.path if v.matched else None,
                "reason": v.reason,
            }
            for v in sorted(report.suspicious, key=lambda v: v.entry.relpath)
        ],
        "skipped_user": list(report.skipped_user),
        "skipped_junk": list(report.skipped_junk),
        "errors": [{"path": str(e.path), "message": e.message} for e in report.errors],
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_render.py -v`
Expected: all PASS. If `test_an_empty_card_is_not_reported_as_all_present` is brittle on the `"all" not in out` assertion, tighten it to `assert "all present" not in out` — the intent is that an empty card never renders the green all-present line.

- [ ] **Step 5: Commit**

```bash
git add src/ftv/render.py tests/test_render.py
git commit -m "feat: Rich rendering for verdicts, scan lists and JSON

Missing files render as a tree following the card's own folder structure.
An empty card gets its own neutral state rather than a green verdict,
since an empty card and a failed mount look identical by file count.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: CLI — scan management commands

**Files:**
- Create: `src/ftv/cli.py`
- Modify: `src/ftv/core/walker.py` (add `walk_trees` for multi-root scans)
- Test: `tests/test_cli_scan.py`, `tests/test_walker.py` (add multi-root cases)

**Interfaces:**
- Consumes: `walk_tree`, `WalkOutcome` (Task 4); `Index`, `ScanExistsError`, `ScanNotFoundError` (Task 6); `normalize_skip_list` (Task 3); `render_scan_list`, `human_bytes` (Task 12); `default_db_path` (Task 5)
- Produces: `walk_trees(roots, skip_extensions) -> WalkOutcome`; the Typer `app` with a `scan` sub-app exposing `add`, `list`, `refresh`, `rm`; and the shared `--db` option. Task 14 adds `verify` to the same `app`.

- [ ] **Step 1: Write the failing test for `walk_trees`**

Append to `tests/test_walker.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_walker.py -k walk_trees -v`
Expected: FAIL — `ImportError: cannot import name 'walk_trees'`

- [ ] **Step 3: Add `walk_trees` to `src/ftv/core/walker.py`**

Append to the module:

```python
def walk_trees(roots: Iterable[Path], skip_extensions: frozenset[str]) -> WalkOutcome:
    """Walk several roots and merge the outcomes.

    A scan may cover more than one folder, and entries keep their absolute
    paths so identically-named files in different roots stay distinct.
    """
    entries: list[FileEntry] = []
    skipped_user: list[str] = []
    skipped_junk: list[str] = []
    errors: list[WalkError] = []

    for root in roots:
        outcome = walk_tree(Path(root), skip_extensions)
        entries.extend(outcome.entries)
        skipped_user.extend(outcome.skipped_user)
        skipped_junk.extend(outcome.skipped_junk)
        errors.extend(outcome.errors)

    return WalkOutcome(
        entries=tuple(entries),
        skipped_user=tuple(skipped_user),
        skipped_junk=tuple(skipped_junk),
        errors=tuple(errors),
    )
```

Add `Iterable` to the existing `collections.abc` import at the top of the file:

```python
from collections.abc import Iterable
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/test_walker.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing test for the scan commands**

Create `tests/test_cli_scan.py`:

```python
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ftv.cli import app
from ftv.core.index import Index

runner = CliRunner()


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
    result = invoke(
        "scan", "add", "S", str(destination), "--skip", ".NEF", "--db", str(db)
    )
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


def test_scan_refresh_reports_an_unknown_name(db: Path):
    result = invoke("scan", "refresh", "nope", "--db", str(db))
    assert result.exit_code != 0
    assert "nope" in result.output


def test_scan_refresh_refuses_when_a_root_is_not_mounted(db: Path, destination: Path, tmp_path: Path):
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


def test_scan_rm_reports_an_unknown_name(db: Path):
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


def test_scan_list_json_is_machine_readable(db: Path, destination: Path):
    invoke("scan", "add", "Video SSD", str(destination), "--db", str(db))
    result = invoke("scan", "list", "--json", "--db", str(db))

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["name"] == "Video SSD"
    assert payload[0]["file_count"] == 3
    assert payload[0]["roots"] == [str(destination.resolve())]
```

- [ ] **Step 6: Run to verify it fails**

Run: `poetry run pytest tests/test_cli_scan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.cli'`

- [ ] **Step 7: Write `src/ftv/cli.py`**

```python
"""The command line surface.

Argument parsing and orchestration only. Everything that decides anything
lives in ftv.core; everything that prints lives in ftv.render.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from ftv.core.filters import normalize_skip_list
from ftv.core.index import Index, ScanExistsError, ScanNotFoundError
from ftv.core.verify import DestinationUnavailableError, check_destination_available
from ftv.core.walker import walk_trees
from ftv.paths import DbPathError, default_db_path
from ftv.render import render_scan_list

app = typer.Typer(
    add_completion=False,
    help="Verify every file on an SD card was transferred before you format it.",
)
scan_app = typer.Typer(add_completion=False, help="Manage remembered destination scans.")
app.add_typer(scan_app, name="scan")

console = Console()
err_console = Console(stderr=True)

DbOption = Annotated[
    Path | None,
    typer.Option("--db", help="Index database location. Defaults to the user data directory."),
]


def _open_index(db: Path | None, roots: list[Path]) -> Index:
    try:
        return Index.open(db or default_db_path(), roots=roots)
    except DbPathError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc


def _require_scan(index: Index, name: str):
    scan = index.get_scan(name)
    if scan is None:
        console.print(f"[red]no scan named {name!r}[/red]")
        available = [s.name for s in index.list_scans()]
        if available:
            console.print("Available scans: " + ", ".join(available))
        raise typer.Exit(code=2)
    return scan


def _report_walk_problems(outcome) -> None:
    if outcome.errors:
        console.print(
            f"[yellow]{len(outcome.errors):,} unreadable path"
            f"{'s' if len(outcome.errors) != 1 else ''} during the scan:[/yellow]"
        )
        for error in outcome.errors[:20]:
            console.print(f"  {error.path}  [dim]{error.message}[/dim]")
        if len(outcome.errors) > 20:
            console.print(f"  [dim]… and {len(outcome.errors) - 20:,} more[/dim]")
        console.print(
            "[dim]Files under those paths are not in this scan, so a card holding them "
            "will report them missing.[/dim]"
        )


@scan_app.command("add")
def scan_add(
    name: Annotated[str, typer.Argument(help="A name you will pick this scan by.")],
    paths: Annotated[list[Path], typer.Argument(help="One or more destination folders.")],
    db: DbOption = None,
    skip: Annotated[
        list[str] | None,
        typer.Option("--skip", help="Extensions you do not keep here, e.g. --skip .NEF,.CR3"),
    ] = None,
    use_mtime: Annotated[
        bool,
        typer.Option(
            "--mtime/--no-mtime",
            help="Use modification time as a confidence signal. Turn off if your copy tool "
            "does not preserve timestamps.",
        ),
    ] = True,
) -> None:
    """Scan destination folders and remember what is in them."""
    roots = [p.expanduser().resolve() for p in paths]
    missing = [str(r) for r in roots if not r.is_dir()]
    if missing:
        console.print("[red]not a readable folder: " + ", ".join(missing) + "[/red]")
        raise typer.Exit(code=2)

    skips = normalize_skip_list(skip)
    index = _open_index(db, roots)
    try:
        with console.status(f"Scanning {len(roots)} folder(s)…"):
            outcome = walk_trees(roots, skips)
        scan = index.save_scan(
            name, roots, outcome.entries, use_mtime=use_mtime, skip_extensions=skips
        )
    except ScanExistsError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    finally:
        index.close()

    console.print(
        f"[green]Scanned[/green] [bold]{scan.name}[/bold]: {scan.file_count:,} files "
        f"across {len(roots)} folder(s)"
    )
    if outcome.skipped_user:
        console.print(f"[dim]skipped by your skip list: {len(outcome.skipped_user):,}[/dim]")
    if outcome.skipped_junk:
        console.print(f"[dim]skipped as junk: {len(outcome.skipped_junk):,}[/dim]")
    _report_walk_problems(outcome)


@scan_app.command("list")
def scan_list(
    db: DbOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """List the remembered scans."""
    index = _open_index(db, [])
    try:
        scans = index.list_scans()
    finally:
        index.close()

    if as_json:
        console.print_json(
            json.dumps(
                [
                    {
                        "name": s.name,
                        "roots": [str(r) for r in s.roots],
                        "scanned_at": s.scanned_at.isoformat(),
                        "use_mtime": s.use_mtime,
                        "skip_extensions": sorted(s.skip_extensions),
                        "file_count": s.file_count,
                    }
                    for s in scans
                ]
            )
        )
        return

    render_scan_list(console, scans)


@scan_app.command("refresh")
def scan_refresh(
    name: Annotated[str, typer.Argument(help="The scan to re-walk.")],
    db: DbOption = None,
) -> None:
    """Re-walk a scan's folders, keeping its skip list and mtime setting."""
    index = _open_index(db, [])
    try:
        scan = _require_scan(index, name)
        try:
            check_destination_available(scan)
        except DestinationUnavailableError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc

        with console.status(f"Re-scanning {scan.name}…"):
            outcome = walk_trees(scan.roots, scan.skip_extensions)
        refreshed = index.save_scan(
            scan.name,
            scan.roots,
            outcome.entries,
            use_mtime=scan.use_mtime,
            skip_extensions=scan.skip_extensions,
            replace=True,
        )
    except ScanNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    finally:
        index.close()

    console.print(
        f"[green]Refreshed[/green] [bold]{refreshed.name}[/bold]: {refreshed.file_count:,} files"
    )
    _report_walk_problems(outcome)


@scan_app.command("rm")
def scan_rm(
    name: Annotated[str, typer.Argument(help="The scan to delete.")],
    db: DbOption = None,
) -> None:
    """Forget a scan. Touches nothing on the destination."""
    index = _open_index(db, [])
    try:
        if not index.delete_scan(name):
            console.print(f"[red]no scan named {name!r}[/red]")
            raise typer.Exit(code=2)
    finally:
        index.close()

    console.print(f"[green]Deleted[/green] scan [bold]{name}[/bold]")
```

- [ ] **Step 8: Run to verify it passes**

Run: `poetry run pytest tests/test_cli_scan.py -v`
Expected: all PASS. If the `--db` refusal test reports exit code 1 rather than 2, Typer wrapped the exception — assert `result.exit_code != 0` and keep the message assertion.

- [ ] **Step 9: Verify the installed entry point works**

Run: `poetry run ftv scan list --help`
Expected: help text listing the `scan` commands.

- [ ] **Step 10: Commit**

```bash
git add src/ftv/cli.py src/ftv/core/walker.py tests/test_cli_scan.py tests/test_walker.py
git commit -m "feat: scan management commands

scan add/list/refresh/rm, with multi-root scans via walk_trees. Refresh
preserves the scan's skip list and mtime setting, and refuses to run when
a root is not mounted rather than silently storing an empty scan.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: The verify session loop

**Files:**
- Create: `src/ftv/session.py`
- Modify: `src/ftv/cli.py` (add the `verify` command)
- Test: `tests/test_session.py`, `tests/test_cli_verify.py`

**Interfaces:**
- Consumes: `verify_card`, `check_destination_available`, `DestinationUnavailableError`, `suggests_disabling_mtime` (Task 10); `DeepScope` (Task 9); `Volume`, `VolumeDetector`, `FakeVolumeDetector`, `wait_for_new_volume` (Task 11); `render_report`, `render_card_header`, `report_to_dict` (Task 12); `Index` (Task 6)
- Produces: `SessionResult(cards_verified, cards_safe, exit_code)` and `run_session(...) -> SessionResult`; the `ftv verify` command.

Every collaborator that would otherwise make the loop untestable — the detector, the sleep, the confirmation prompt, the "another card?" prompt — is injected. That is what lets a three-card session run deterministically in a unit test.

**On `--deep[=all]`:** implemented with Click's `flag_value` so bare `--deep` means `suspicious` and `--deep=all` means `all`. If the installed Typer does not forward `flag_value`, fall back to two boolean options, `--deep` and `--deep-all`, and update the two CLI tests that exercise the spelling. The behaviour matters; the spelling does not.

- [ ] **Step 1: Write the failing test for the session loop**

Create `tests/test_session.py`:

```python
from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.console import Console

from ftv.core.deephash import DeepScope
from ftv.core.models import IndexedFile, Scan
from ftv.session import SessionResult, run_session
from ftv.volumes import FakeVolumeDetector, Volume


class FakeLookup:
    def __init__(self, files: list[IndexedFile] | None = None) -> None:
        self.files = files or []

    def find(self, name: str, size: int) -> list[IndexedFile]:
        return [f for f in self.files if f.name == name and f.size == size]


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    dest = tmp_path / "ssd"
    dest.mkdir()
    return dest


def make_scan(destination: Path) -> Scan:
    return Scan(
        name="Video SSD",
        roots=(destination,),
        scanned_at=datetime(2026, 9, 14, tzinfo=UTC),
        use_mtime=True,
    )


def make_card(tmp_path: Path, label: str, names: list[str]) -> Volume:
    root = tmp_path / label
    (root / "DCIM").mkdir(parents=True)
    for name in names:
        (root / "DCIM" / name).write_bytes(name.encode() * 4)
    return Volume(path=root, name=label, total_bytes=1_000_000)


def index_card(destination: Path, card: Volume) -> list[IndexedFile]:
    """Mirror every file on the card into the destination and index it."""
    import os

    out = []
    for source in sorted((card.path / "DCIM").iterdir()):
        target = destination / source.name
        target.write_bytes(source.read_bytes())
        stat = source.stat()
        os.utime(target, (stat.st_atime, stat.st_mtime))
        out.append(
            IndexedFile(path=str(target), name=source.name, size=stat.st_size, mtime=stat.st_mtime)
        )
    return out


def console() -> Console:
    return Console(record=True, width=100, force_terminal=False, no_color=True)


def run(detector, scan, lookup, con, **kwargs) -> SessionResult:
    """Drive a session with every real-world collaborator replaced.

    `wait_polls=3` bounds the wait so a test can never hang, and the scripted
    detector advances exactly one state per poll — which only holds because the
    session polls the detector once per cycle and never out of band.
    """
    defaults = dict(
        confirm_card=lambda volume: True,
        ask_continue=lambda: False,
        sleeper=lambda _: None,
        poll_interval=0,
        wait_polls=3,
    )
    defaults.update(kwargs)
    return run_session(scan=scan, lookup=lookup, detector=detector, console=con, **defaults)


def test_one_fully_transferred_card_is_reported_safe(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "NIKON Z8", ["A.JPG", "B.MP4"])
    lookup = FakeLookup(index_card(destination, card))
    con = console()

    result = run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, con)

    assert result.cards_verified == 1
    assert result.cards_safe == 1
    assert result.exit_code == 0
    assert "safe to format" in con.export_text().lower()


def test_a_card_with_a_missing_file_sets_a_failing_exit_code(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "SONY A7", ["A.JPG", "B.MP4"])
    indexed = index_card(destination, card)
    lookup = FakeLookup(indexed[:1])
    con = console()

    result = run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, con)

    assert result.cards_verified == 1
    assert result.cards_safe == 0
    assert result.exit_code == 1
    assert "B.MP4" in con.export_text()


def test_several_cards_run_one_after_another(tmp_path: Path, destination: Path):
    first = make_card(tmp_path, "CARD1", ["A.JPG"])
    second = make_card(tmp_path, "CARD2", ["B.JPG"])
    third = make_card(tmp_path, "CARD3", ["C.JPG"])
    lookup = FakeLookup(
        index_card(destination, first)
        + index_card(destination, second)
        + index_card(destination, third)
    )
    detector = FakeVolumeDetector([[first], [second], [third]])
    answers = iter([True, True, False])
    con = console()

    result = run(
        detector, make_scan(destination), lookup, con, ask_continue=lambda: next(answers)
    )

    assert result.cards_verified == 3
    assert result.cards_safe == 3
    assert result.exit_code == 0


def test_a_failing_card_makes_the_whole_session_fail(tmp_path: Path, destination: Path):
    good = make_card(tmp_path, "GOOD", ["A.JPG"])
    bad = make_card(tmp_path, "BAD", ["B.JPG"])
    lookup = FakeLookup(index_card(destination, good))
    detector = FakeVolumeDetector([[good], [bad]])
    answers = iter([True, False])
    con = console()

    result = run(
        detector, make_scan(destination), lookup, con, ask_continue=lambda: next(answers)
    )

    assert result.cards_verified == 2
    assert result.cards_safe == 1
    assert result.exit_code == 1, "one bad card must fail the session"


def test_declining_a_volume_skips_it_without_verifying(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "TIME MACHINE", ["A.JPG"])
    con = console()

    result = run(
        FakeVolumeDetector([[card]]),
        make_scan(destination),
        FakeLookup([]),
        con,
        confirm_card=lambda volume: False,
    )

    assert result.cards_verified == 0
    assert result.exit_code == 0, "skipping a drive is not a failure"


def test_a_declined_volume_is_not_offered_again(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "TIME MACHINE", ["A.JPG"])
    asked: list[str] = []

    def confirm(volume: Volume) -> bool:
        asked.append(volume.name)
        return False

    run(
        FakeVolumeDetector([[card]]),
        make_scan(destination),
        FakeLookup([]),
        console(),
        confirm_card=confirm,
    )

    assert asked == ["TIME MACHINE"], "the same drive must not be re-offered in a loop"


def test_the_confirmation_prompt_sees_the_volume_name_and_size(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "NIKON Z8", ["A.JPG"])
    seen: list[Volume] = []

    run(
        FakeVolumeDetector([[card]]),
        make_scan(destination),
        FakeLookup([]),
        console(),
        confirm_card=lambda volume: (seen.append(volume), False)[1],
    )

    assert seen[0].name == "NIKON Z8"
    assert seen[0].total_bytes == 1_000_000


def test_the_session_ends_when_no_card_appears(destination: Path):
    con = console()
    result = run(FakeVolumeDetector([[]]), make_scan(destination), FakeLookup([]), con)

    assert result.cards_verified == 0
    assert result.exit_code == 0


def test_an_already_mounted_card_is_offered_immediately(tmp_path: Path, destination: Path):
    """Launching with the card already in must not wait forever."""
    card = make_card(tmp_path, "ALREADY IN", ["A.JPG"])
    lookup = FakeLookup(index_card(destination, card))

    result = run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, console())
    assert result.cards_verified == 1


def test_a_card_reinserted_after_ejection_is_offered_again(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "CARD", ["A.JPG"])
    lookup = FakeLookup(index_card(destination, card))
    detector = FakeVolumeDetector([[card], [], [card]])
    answers = iter([True, False])

    result = run(
        detector, make_scan(destination), lookup, console(), ask_continue=lambda: next(answers)
    )
    assert result.cards_verified == 2


def test_deep_scope_is_passed_through_to_verification(tmp_path: Path, destination: Path):
    import os

    card = make_card(tmp_path, "CARD", ["A.JPG"])
    indexed = index_card(destination, card)
    # Same name, same size, same mtime, different bytes.
    Path(indexed[0].path).write_bytes(b"ZZZZZ"[: indexed[0].size])
    os.utime(indexed[0].path, (indexed[0].mtime, indexed[0].mtime))
    lookup = FakeLookup(indexed)

    shallow = run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, console())
    assert shallow.exit_code == 0

    deep = run(
        FakeVolumeDetector([[card]]),
        make_scan(destination),
        lookup,
        console(),
        deep=DeepScope.ALL,
    )
    assert deep.exit_code == 1


def test_the_card_header_is_printed_before_the_verdict(tmp_path: Path, destination: Path):
    card = make_card(tmp_path, "NIKON Z8", ["A.JPG"])
    lookup = FakeLookup(index_card(destination, card))
    con = console()

    run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, con)
    out = con.export_text()

    assert "NIKON Z8" in out
    assert out.index("NIKON Z8") < out.lower().index("safe to format")


def test_json_mode_prints_one_object_per_card_and_no_decoration(
    tmp_path: Path, destination: Path
):
    import json

    card = make_card(tmp_path, "CARD", ["A.JPG"])
    lookup = FakeLookup(index_card(destination, card))
    con = console()

    run(FakeVolumeDetector([[card]]), make_scan(destination), lookup, con, as_json=True)
    out = con.export_text().strip()

    payload = json.loads(out)
    assert payload["safe_to_format"] is True
    assert "safe to format" not in out.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `poetry run pytest tests/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ftv.session'`

- [ ] **Step 3: Write `src/ftv/session.py`**

```python
"""The resident verify loop: pick a scan once, then work through cards.

Every collaborator that would make this untestable is injected — the detector,
the sleep, and both prompts — so a multi-card session runs deterministically
in a unit test with no terminal and no real time.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from ftv.core.deephash import DeepScope
from ftv.core.models import Lookup, Scan
from ftv.core.verify import suggests_disabling_mtime, verify_card
from ftv.render import render_card_header, render_report, report_to_dict
from ftv.volumes import Volume, VolumeDetector, wait_for_new_volume

DEFAULT_WAIT_POLLS = 600


@dataclass(frozen=True, slots=True)
class SessionResult:
    cards_verified: int
    cards_safe: int
    exit_code: int


def run_session(
    *,
    scan: Scan,
    lookup: Lookup,
    detector: VolumeDetector,
    console: Console,
    confirm_card: Callable[[Volume], bool],
    ask_continue: Callable[[], bool],
    deep: DeepScope = DeepScope.NONE,
    skip_override: frozenset[str] | None = None,
    as_json: bool = False,
    poll_interval: float = 1.0,
    sleeper: Callable[[float], None] | None = None,
    wait_polls: int | None = DEFAULT_WAIT_POLLS,
) -> SessionResult:
    """Verify cards until the user stops or nothing new appears.

    Volumes already mounted at start are offered immediately, so launching with
    the card already inserted does not wait. A volume that is ejected drops out
    of the seen set, so re-inserting it is treated as a new card.
    """
    import time

    sleep = sleeper if sleeper is not None else time.sleep
    seen: set[Path] = set()
    verified = 0
    safe = 0

    while True:
        # wait_for_new_volume prunes ejected volumes from `seen` as it polls,
        # so the detector is queried exactly once per cycle.
        volume = wait_for_new_volume(
            detector,
            seen=seen,
            poll_interval=poll_interval,
            sleeper=sleep,
            stop_after=wait_polls,
        )
        if volume is None:
            break

        seen.add(volume.path)

        if not confirm_card(volume):
            continue

        if not as_json:
            render_card_header(console, scan, volume)

        report = verify_card(
            volume.path, scan, lookup, deep=deep, skip_override=skip_override
        )
        verified += 1
        if report.safe_to_format:
            safe += 1

        if as_json:
            console.print_json(json.dumps(report_to_dict(report)))
        else:
            render_report(
                console, report, scan, suggest_no_mtime=suggests_disabling_mtime(report)
            )

        if not ask_continue():
            break

    return SessionResult(
        cards_verified=verified,
        cards_safe=safe,
        exit_code=0 if verified == safe else 1,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `poetry run pytest tests/test_session.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing CLI test**

Create `tests/test_cli_verify.py`:

```python
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
        ["scan", "add", "Video SSD", str(workspace["destination"]), "--db", str(workspace["db"]), *extra],
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

    deep = verify(workspace, "--skip", ".NEF", "--deep=all")
    assert deep.exit_code == 1
    assert "A.JPG" in deep.output


def test_verify_prints_the_scans_age(workspace):
    add_scan(workspace)
    result = verify(workspace)
    assert "just now" in result.output or "ago" in result.output
```

- [ ] **Step 6: Run to verify it fails**

Run: `poetry run pytest tests/test_cli_verify.py -v`
Expected: FAIL — `No such command 'verify'`

- [ ] **Step 7: Add the `verify` command to `src/ftv/cli.py`**

Add these imports at the top:

```python
import time

from ftv.core.deephash import DeepScope
from ftv.core.verify import verify_card
from ftv.render import render_card_header, render_report, report_to_dict
from ftv.session import run_session
from ftv.volumes import MacOSVolumeDetector, Volume
```

Then append the command:

```python
@app.command("verify")
def verify(
    scan_name: Annotated[str, typer.Option("--scan", help="The remembered scan to check against.")],
    db: DbOption = None,
    path: Annotated[
        Path | None,
        typer.Option("--path", help="Verify this folder once and exit, skipping detection."),
    ] = None,
    deep: Annotated[
        str | None,
        typer.Option(
            "--deep",
            flag_value="suspicious",
            help="Content-verify matched pairs. Bare --deep does the suspicious ones; "
            "--deep=all does every match and takes minutes per card.",
        ),
    ] = None,
    rescan: Annotated[
        bool, typer.Option("--rescan", help="Refresh the scan before verifying.")
    ] = False,
    skip: Annotated[
        list[str] | None,
        typer.Option("--skip", help="Override the scan's skip list for this run."),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask before verifying a detected volume.")
    ] = False,
) -> None:
    """Check a card against a remembered scan."""
    try:
        scope = DeepScope.from_flag(deep)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    skip_override = normalize_skip_list(skip) if skip else None

    index = _open_index(db, [])
    try:
        scan = _require_scan(index, scan_name)

        try:
            check_destination_available(scan)
        except DestinationUnavailableError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc

        if rescan:
            with console.status(f"Re-scanning {scan.name}…"):
                outcome = walk_trees(scan.roots, scan.skip_extensions)
            scan = index.save_scan(
                scan.name,
                scan.roots,
                outcome.entries,
                use_mtime=scan.use_mtime,
                skip_extensions=scan.skip_extensions,
                replace=True,
            )

        lookup = index.lookup_for(scan.name)

        if path is not None:
            card_root = path.expanduser().resolve()
            if not card_root.is_dir():
                console.print(f"[red]not a readable folder: {card_root}[/red]")
                raise typer.Exit(code=2)

            report = verify_card(
                card_root, scan, lookup, deep=scope, skip_override=skip_override
            )
            if as_json:
                console.print_json(json.dumps(report_to_dict(report)))
            else:
                render_card_header(
                    console,
                    scan,
                    Volume(path=card_root, name=card_root.name, total_bytes=0),
                )
                render_report(
                    console, report, scan, suggest_no_mtime=suggests_disabling_mtime(report)
                )
            raise typer.Exit(code=report.exit_code)

        result = run_session(
            scan=scan,
            lookup=lookup,
            detector=MacOSVolumeDetector(),
            console=console,
            confirm_card=(
                (lambda volume: True)
                if yes
                else (
                    lambda volume: typer.confirm(
                        f"Verify {volume.name} ({human_bytes(volume.total_bytes)})?",
                        default=True,
                    )
                )
            ),
            ask_continue=lambda: typer.confirm(
                "Eject it and insert the next card. Continue?", default=True
            ),
            deep=scope,
            skip_override=skip_override,
            as_json=as_json,
            sleeper=time.sleep,
        )
    finally:
        index.close()

    raise typer.Exit(code=result.exit_code)
```

Add `human_bytes` and `suggests_disabling_mtime` to the imports:

```python
from ftv.core.verify import suggests_disabling_mtime
from ftv.render import human_bytes
```

- [ ] **Step 8: Run to verify it passes**

Run: `poetry run pytest tests/test_cli_verify.py -v`
Expected: all PASS.

If `flag_value` is not forwarded by the installed Typer, `--deep` will fail to parse. In that case replace the option with two booleans:

```python
    deep: Annotated[bool, typer.Option("--deep", help="Content-verify the suspicious pairs.")] = False,
    deep_all: Annotated[bool, typer.Option("--deep-all", help="Content-verify every match.")] = False,
```

and derive `scope = DeepScope.ALL if deep_all else (DeepScope.SUSPICIOUS if deep else DeepScope.NONE)`. Update `test_deep_all_catches_a_same_size_different_content_copy` to pass `--deep-all`.

- [ ] **Step 9: Run the whole suite**

Run: `poetry run pytest -v && poetry run ruff check .`
Expected: everything PASS, Ruff clean.

- [ ] **Step 10: Commit**

```bash
git add src/ftv/session.py src/ftv/cli.py tests/test_session.py tests/test_cli_verify.py
git commit -m "feat: verify command and resident card session

Detector, sleep and both prompts are injected, so a multi-card session
runs deterministically in tests. Cards already mounted at launch are
offered immediately; an ejected card is offered again when reinserted.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 15: End-to-end proof and the mutation-detection invariant

**Files:**
- Create: `tests/test_end_to_end.py`
- Create: `tests/test_no_mutation.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything built so far, through the CLI only
- Produces: no new production interfaces. This task proves the assembled tool behaves as specified and that it never modified anything.

These are the two tests the whole design exists to make possible, so they go last and they go through the real CLI rather than through internals.

- [ ] **Step 1: Write the end-to-end test**

Create `tests/test_end_to_end.py`:

```python
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
```

- [ ] **Step 2: Run it**

Run: `poetry run pytest tests/test_end_to_end.py -v`
Expected: all PASS.

- [ ] **Step 3: Write the mutation-detection invariant test**

Create `tests/test_no_mutation.py`:

```python
"""Proves the tool modified nothing on the card or the destination.

This is the executable form of the project's primary constraint. It records a
full manifest of both trees, runs the heaviest verification path available
(--deep=all, the only path that opens file contents), then re-records and
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
         "--db", str(world["db"]), "--deep=all"],
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
         "--db", str(world["db"]), "--deep=all"],
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
         "--db", str(world["db"]), "--deep=all"],
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
             "--db", str(world["db"]), "--deep=all", "--json"],
        )
        assert result.exit_code in (0, 1), result.output
        assert '"error": 0' in result.output or '"error":0' in result.output
    finally:
        for path, mode in original_modes.items():
            path.chmod(mode)
```

Note: `test_a_read_only_card_verifies_without_error` checks the JSON counts for zero errors. If the JSON formatting makes that assertion brittle, parse it with `json.loads` and assert `payload["counts"]["error"] == 0`.

- [ ] **Step 4: Run it**

Run: `poetry run pytest tests/test_no_mutation.py -v`
Expected: all PASS.

- [ ] **Step 5: Prove the mutation test actually catches a mutation**

Temporarily add to `src/ftv/core/verify.py`, inside `verify_card`, before the return:

```python
    (card_root / "_mutation_probe.txt").write_text("this should be caught")
```

Run: `poetry run pytest tests/test_no_mutation.py -v`
Expected: FAIL on "the card was modified" and on `test_verification_creates_no_new_paths`.

Also run: `poetry run pytest tests/test_readonly_invariant.py -v`
Expected: FAIL — the AST audit catches `write_text` in `core`.

**Then remove the probe line and re-run both files to confirm they pass.** Both layers of the invariant have now been shown to work.

- [ ] **Step 6: Run the complete suite**

Run: `poetry run pytest -v && poetry run ruff check . && poetry run ruff format --check .`
Expected: everything PASS.

- [ ] **Step 7: Write the README**

Replace `README.md`:

```markdown
# file-transfer-validator

Verify every file on an SD card was transferred to your SSD before you format it.

Scan a destination folder once; then check card after card against that scan in
under a second each.

## Install

```bash
poetry install
```

## Use

Remember a destination. RAW files that you import into Lightroom rather than
keep on the SSD go on the skip list:

```bash
ftv scan add "Video SSD" /Volumes/SSD/Video /Volumes/SSD/Photo --skip .NEF
ftv scan list
```

Check cards. Insert one, confirm, read the verdict, eject, repeat:

```bash
ftv verify --scan "Video SSD"
```

Check a single folder and exit:

```bash
ftv verify --scan "Video SSD" --path /Volumes/NIKON\ Z8
```

Exit code is `0` only when nothing is missing, nothing is suspicious, and
nothing was unreadable.

| Flag | Effect |
|---|---|
| `--deep` | Content-hash the suspicious pairs to settle them |
| `--deep=all` | Content-hash every match; minutes per card |
| `--rescan` | Refresh the scan before verifying |
| `--skip .NEF` | Override the scan's skip list for this run |
| `--json` | Machine-readable report |
| `--yes` | Do not ask before verifying a detected volume |

Re-scan after copying new files to the destination:

```bash
ftv scan refresh "Video SSD"
```

## How it decides

A card file counts as transferred when a file in the scan has the **same
basename and byte size**, and that file is confirmed still on disk right now.
Matching ignores paths, so reorganising your SSD never invalidates a scan.

Modification time is a confidence signal, not part of the key. Two cards from
one camera both hold `DSC_0001.NEF`; if name and size match but the capture
time does not, the file is reported **suspicious** rather than passed. Use
`--deep` to settle those by content. If your copy tool does not preserve
timestamps, scan with `--no-mtime`.

Every file on the card is accounted for, minus built-in camera and OS junk and
minus your skip list. Both are counted in the verdict, so an exclusion is never
invisible.

## Read-only

**The tool never modifies anything on a card or in a destination.** The only
file it writes is its own index database, under your user data directory.

This is enforced, not just intended: all data access funnels through one
read-only module, an AST audit rejects write-capable calls anywhere in the
core, and a test records both trees before and after a full `--deep=all` run
and asserts nothing changed.

Reading a file can cause the operating system to update its access time on some
filesystems. That is a kernel effect of reading, not an action the tool takes.

Formatting cards is not part of this tool.

## Develop

```bash
poetry run pytest
poetry run ruff check .
```

See `docs/superpowers/specs/` for the design and `docs/superpowers/plans/` for
the implementation plan.
```

- [ ] **Step 8: Commit**

```bash
git add tests/test_end_to_end.py tests/test_no_mutation.py README.md
git commit -m "test: end-to-end proof and the no-mutation invariant

One card exercising every bucket at once through the real CLI, plus the
executable form of the read-only constraint: full manifests of both trees
before and after a --deep=all run, asserted identical.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Manual verification before you trust it

Automated tests cannot cover mount behaviour. Before relying on this tool to
decide whether to format anything, do this once with a real card:

1. `ftv scan add "Real SSD" /path/to/your/real/destination`
2. Insert a card you know is fully backed up. Run `ftv verify --scan "Real SSD"`.
   Expect a green verdict.
3. Insert a card holding at least one file you know was never copied. Expect it
   named in the missing tree.
4. Check that the detected volume is the card and not another drive.
5. Confirm the scan's skip list matches how you actually file things.

The spec deliberately treats a green verdict as a claim worth earning. Run a
few real sessions before you act on one.

