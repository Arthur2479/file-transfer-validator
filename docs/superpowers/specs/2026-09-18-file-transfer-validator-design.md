# file-transfer-validator — Design

**Date:** 2026-09-18
**Status:** Approved for planning

## Purpose

Verify that every file on a connected SD card has already been transferred to a
destination folder (an SSD or a folder on the computer), so the card can be
formatted with confidence. The tool answers one question per card: *is anything
on this card not yet backed up?*

The workflow is many cards in a row. A destination folder is scanned once and
remembered; each card is then checked against that remembered scan in well under
a second.

## Hard invariant: read-only

**The tool never modifies any file or directory on an SD card or in a
destination folder.** The only thing it ever writes is its own index database.

This is the project's primary constraint and outranks every other goal
(performance, convenience, features). It is enforced in four layers:

1. **Single choke point.** `core/fsread.py` is the only module permitted to
   touch card or destination paths. It exposes exactly `walk(root)`,
   `stat_file(path)`, and `read_chunks(path)`, and opens files only with mode
   `"rb"`. No other module performs filesystem access on those trees.
2. **Write-surface audit test.** A test scans the source of `core/` (excluding
   `index.py`, which owns the database) for forbidden symbols and fails the
   build if any appear: `write_text`, `write_bytes`, `os.remove`, `os.unlink`,
   `os.rename`, `os.replace`, `os.mkdir`, `os.makedirs`, `shutil.*`,
   `os.chmod`, `os.utime`, `Path.touch`, and `open()` with any mode other than
   `"rb"`.
3. **Mutation-detection test.** An integration test builds a card tree and a
   destination tree in `tmp_path`, records a manifest of every file and
   directory (relative path, size, `mtime_ns`, content hash), runs a full
   verify including `--deep=all`, then re-records and asserts the two manifests
   are identical. `--deep=all` is included deliberately: it is the only code
   path that opens file contents.
4. **Writable-path guard.** The index database is the only file opened for
   writing, and it lives under `platformdirs.user_data_dir("ftv")`. The index
   module refuses a database path located inside a scan root or on a removable
   volume, so the index can never be written onto a card or a destination.

**Known OS-level caveat:** reading a file may cause the operating system to
update that file's access time (`atime`) on some filesystems. This is a kernel
effect of reading, not an action the tool takes; preventing it entirely would
require mounting volumes `noatime`. File contents, sizes, and modification
times are never altered.

## Matching semantics

A card file counts as already transferred when a file in the remembered scan has
the **same basename and the same byte size**, and that file is confirmed to
still exist on disk at that size.

Matching is deliberately **path-agnostic**: only the basename is compared, not
the path relative to the card root. Reorganising the destination into
date-based or shoot-based folders therefore never invalidates a scan. Reading
file contents is not required, so a card verifies in `stat` time rather than
read time.

### Modification time as a confidence signal

Two cards from the same camera both contain files such as
`DCIM/100NZ_8/DSC_0001.NEF` — different photographs with identical names. Once
the first card is on the destination, the second card's same-named file matches
on name, and if the two happen to share a byte size the tool would wrongly
report it as present.

To close this, the scan also records each file's modification time. Cameras set
`mtime` to capture time, and Finder and `rsync` copies preserve it, so a
name-and-size match whose `mtime` also agrees is almost certainly the same file.
A match where name and size agree but `mtime` does not is reported as
**suspicious** rather than silently passing.

`mtime` is a per-scan setting (`use_mtime`, default on). If a copy tool does not
preserve timestamps, every match becomes suspicious; the report detects this
pattern and recommends disabling the signal for that scan instead of listing
thousands of warnings.

### Staleness guard

A remembered scan can go stale in two directions, and only one is dangerous:

- **Files deleted from the destination after scanning** — the index still claims
  they are present, so the tool would call a card safe when it is not. This is
  the dangerous direction and is closed by the confirm step below.
- **Files added to the destination after scanning** — the index does not know
  about them, so they report as `missing`. Harmless (it over-reports), and
  fixed by `ftv scan refresh NAME`.

**The confirm step:** after finding a match in the index, the tool calls `stat`
on that specific destination path to verify the file still exists at that size.
A match that fails confirmation becomes `suspicious`. This costs one `stat` per
card file — on the order of a thousand calls, taking milliseconds — and makes a
green verdict mean *the files are on the destination now*, not *they were when I
last scanned*.

### Why the scan is remembered at all

The confirm step is not a re-scan, and the index is not merely a cache. The
index answers a question a targeted `stat` cannot: *does this file exist
anywhere on the destination?* Asserting `missing` requires knowing the complete
contents of the destination. The index produces the candidate set and makes a
negative verdict assertable; the confirm step only validates the positives.

The cost difference is the reason for the whole design: roughly a thousand
`stat` calls on known paths, versus a full `readdir` walk of a
hundreds-of-thousands-of-entries destination tree before every single card.

For destinations small enough that a full walk is cheap, `ftv verify --rescan`
refreshes the scan before verifying, sidestepping staleness entirely.

## Which files are checked

Every file on the card is accounted for, minus two exclusion sets. An extension
allowlist was rejected: shooting a format that is not on the list would make
those files invisible to the check, which is a silent path to a wrong green
verdict.

**Built-in junk denylist** (camera and OS cruft): `*.THM`, `*.CTG`, `MISC/`,
`.DS_Store`, `System Volume Information/`, `.fseventsd/`, `.Spotlight-V100/`,
`.Trashes/`, `._*` (AppleDouble).

**Per-scan user skip list:** extensions the user deliberately does not keep on
this destination — for example `.NEF` when RAW files are imported into
Lightroom instead. The skip list is stored on the scan, because "RAWs do not
live on this SSD" is a fact about that destination rather than a global
preference. A per-run CLI flag overrides it.

Both exclusion sets are **counted and displayed** in the verdict
(`skipped: 214 .NEF (skip list) · 9 junk`), so an exclusion is never invisible.

## Verdict model

Every card file lands in exactly one bucket:

| Bucket | Meaning |
|---|---|
| `present` | name and size matched, `mtime` agreed (or the signal is off), and the destination file was confirmed on disk |
| `suspicious` | name and size matched but `mtime` disagreed, **or** the matched destination file failed confirmation |
| `missing` | no name-and-size match anywhere in the scan |
| `skipped_user` | matched the scan's skip list |
| `skipped_junk` | matched the built-in junk denylist |
| `error` | could not be read or `stat`ed |

**Safe to format requires `missing`, `suspicious`, and `error` to all be
empty.** Exit code is `0` in exactly that case and `1` otherwise. The rule is
deliberately conservative: anything the tool could not fully resolve blocks the
green verdict rather than being rounded down to acceptable.

Missing files are displayed as a tree following the card's own folder structure.

### Deep verification

`--deep` hashes only the `suspicious` pairs (streamed blake2b over both copies).
This is the targeted case: it resolves exactly the ambiguity that `mtime`
flagged, and usually touches a handful of files.

`--deep=all` hashes every matched pair for a full content proof. This reads
every matched file from both the card and the destination and takes minutes per
card; it is documented as such.

A deep-resolved suspicious file becomes `present` or `missing` outright.

## Architecture

One rule drives the structure: the comparison logic is pure. `compare()` takes
an iterable of card file entries plus any object satisfying a `Lookup` protocol
(`find(name, size) -> list[IndexedFile]`) and returns a `VerifyReport`. It
touches no filesystem, no database, and no terminal. This is the code whose
correctness decides whether a card gets formatted, so it must be testable with
fakes and readable in one sitting.

```
src/ftv/
  cli.py          Typer commands — argument parsing only, no logic
  session.py      the resident verify loop (detect → confirm → verify → wait)
  render.py       VerifyReport → Rich panels and trees; the only module that prints
  core/
    models.py     frozen dataclasses: FileEntry, Scan, Match, VerifyReport
    fsread.py     THE ONLY module that touches card/destination paths; read-only
    filters.py    junk denylist + per-scan skip list → keep/skip/junk decision
    walker.py     directory tree → Iterator[FileEntry], applying filters
    index.py      SQLite: save/load/refresh scans; implements Lookup
    compare.py    PURE: card entries + Lookup → VerifyReport
    confirm.py    re-stat matched destination paths (staleness guard)
    deephash.py   streamed blake2b for --deep
    volumes.py    removable-volume detection, behind a protocol
tests/
```

`volumes.py` is the only platform-specific module, hidden behind a
`VolumeDetector` protocol (`list_removable()`, `watch()`). The macOS
implementation polls `/Volumes` and filters out the boot disk and non-removable
mounts. Linux and Windows support can be added as new implementations without
touching any other module.

## Storage

SQLite, one file at `platformdirs.user_data_dir("ftv")/index.db`.

```sql
scans (
  id              INTEGER PRIMARY KEY,
  name            TEXT UNIQUE NOT NULL,
  scanned_at      TEXT NOT NULL,
  use_mtime       INTEGER NOT NULL DEFAULT 1,
  skip_extensions TEXT NOT NULL              -- JSON array
);

scan_roots (
  scan_id INTEGER NOT NULL REFERENCES scans(id),
  path    TEXT NOT NULL
);

files (
  scan_id INTEGER NOT NULL REFERENCES scans(id),
  path    TEXT NOT NULL,
  name    TEXT NOT NULL,
  size    INTEGER NOT NULL,
  mtime   REAL NOT NULL
);

CREATE INDEX files_lookup ON files (scan_id, name, size);
```

The `files_lookup` index turns each card file into one O(log n) probe, so a
1,200-file card against a 500,000-file index resolves in well under a second.

`scan_roots` is a separate table because one named scan may cover several
folders.

SQLite was chosen over a JSON file because a JSON index must be fully parsed
into memory on every run and fully rewritten on every re-scan, and a crash
mid-rewrite leaves a corrupt index backing a "safe to format" decision. SQLite
provides indexed lookups, atomic transactions, and room for incremental
re-scans. The cost is schema migrations, handled with the `user_version` pragma
and an ordered migration list.

## Command surface

```
ftv scan add NAME PATH...   --skip .NEF,.CR3   --no-mtime
ftv scan list               names, roots, file count, scan age
ftv scan refresh NAME       re-walk the roots
ftv scan rm NAME

ftv verify --scan NAME      resident session (default behaviour)
                            --path P        one-shot against a given path, no detection
                            --deep          hash the suspicious pairs
                            --deep=all      hash every matched pair
                            --rescan        refresh the scan before verifying
                            --skip .NEF     override the scan's skip list for this run
                            --json          machine-readable report
```

## Interaction design

Typer provides the commands, Rich the output. There is no full-screen TUI
framework: the card workflow is linear rather than exploratory, so a TUI would
add an app and widget layer plus an async test harness for a flow with one
decision per card. Rich still supplies progress bars, coloured verdicts, and
tree rendering.

**Session flow:** select the scan once, then loop — wait for a removable volume
to mount, ask `Verify NIKON Z8 (312 GB)? [Y/n]`, show progress, print the
verdict, prompt to eject and insert the next card, wait again.

Card detection is automatic but **always confirmed before verifying**. One
keystroke per card, in exchange for never touching a drive the user did not mean
to check. `--path` bypasses detection entirely.

```
$ ftv verify --scan "Video SSD"

Scan: Video SSD   scanned 3 days ago · 48,213 files
Card: NIKON Z8    /Volumes/NIKON Z8 · 1,204 files · 312 GB

Verifying  ####################  100%

   ALL 1,204 FILES PRESENT — safe to format

   skipped: 214 .NEF (skip list) · 9 junk

Insert next card and press Enter  (q to quit)
```

## Error handling

- **Scan roots absent at verify time.** If the destination is not connected, the
  confirm step cannot run. Refuse to verify, loudly. Without this, an unplugged
  destination would fail every confirmation and turn a green card into a wall of
  suspicious files.
- **Card unmounted mid-verify.** Abort that card with a clear message and return
  to waiting. Never report a partial verdict.
- **Zero files on card after filtering.** Reported as a distinct neutral state
  ("no files to verify"), never as a green "all present". An empty card and a
  card that failed to mount correctly are indistinguishable from a file count of
  zero, so that state does not get to imply safety.
- **Unreadable file or directory during the card walk.** Collected into the
  `error` bucket, never silently dropped — and `error` being non-empty blocks a
  green verdict.
- **Every match suspicious.** The signature of a copy tool that does not
  preserve timestamps. The report says so and suggests `--no-mtime` for that
  scan, rather than listing every file.
- **Unknown scan name.** List the available scans.

## Testing

Test-driven throughout, with pytest. The layering exists to make this cheap.

- **`compare.py`** — pure function against a fake `Lookup`. Collision cases,
  `mtime` disagreement, and bucket assignment are exercised here with no disk
  involved. The bulk of the suite.
- **`walker.py` / `filters.py`** — real trees in `tmp_path`: nested folders,
  junk files, skip-list extensions, unreadable directories.
- **`index.py`** — in-memory SQLite, plus a round-trip test and a migration
  test.
- **`volumes.py`** — the protocol gets a fake detector. The macOS
  implementation gets thin tests plus one manual check against a real card,
  since mount behaviour is not honestly unit-testable.
- **`session.py` / `cli.py`** — Typer's `CliRunner`, asserting on rendered text
  and exit codes. A scripted fake detector drives a three-card session to prove
  the loop.
- **Read-only invariant** — the write-surface audit test and the
  mutation-detection test described under *Hard invariant*.

**The end-to-end test that matters most:** a fake destination tree and a fake
card tree in `tmp_path` containing one genuinely missing file, one
same-name/same-size/different-`mtime` collision, and one skip-listed RAW — then
assert the exact bucket contents and exit code 1.

## Technical choices

- Python 3.12+, Poetry, `src/` layout.
- Typer (CLI), Rich (output), platformdirs (paths), SQLite (stdlib).
- Ruff for lint and format, pytest for tests.
- macOS first. The `VolumeDetector` protocol keeps Linux and Windows as a later
  addition rather than a rewrite.

## Out of scope

**Formatting cards.** Not part of this project. The seam is already in place:
`ftv verify` exiting `0` is precisely the gate a future format step would
consume, which is why exit `0` requires `missing`, `suspicious`, and `error` to
all be empty.

When formatting is designed, it will need three things beyond that exit code:
the verdict must come from a card still mounted at that moment rather than a
remembered result; the device identity must be re-checked immediately before the
destructive call, so that an eject-and-swap cannot redirect it; and confirmation
must be the typed volume name rather than a `[y/N]` prompt. It deserves its own
design round, and the verification half should be trusted through real card
sessions first.

Also out of scope: copying or repairing files, incremental re-scan by directory
`mtime`, and any GUI beyond the terminal.
