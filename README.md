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
| `--deep-all` | Content-hash every match; minutes per card |
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
core, and a test records both trees before and after a full `--deep-all` run
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
