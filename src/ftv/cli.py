"""The command line surface.

Argument parsing and orchestration only. Everything that decides anything
lives in ftv.core; everything that prints lives in ftv.render.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape

from ftv.core.deephash import DeepScope
from ftv.core.filters import normalize_skip_list
from ftv.core.index import Index, ScanExistsError, ScanNotFoundError
from ftv.core.verify import (
    DestinationUnavailableError,
    check_destination_available,
    suggests_disabling_mtime,
    verify_card,
)
from ftv.core.walker import walk_trees
from ftv.paths import DbPathError, default_db_path, validate_db_path
from ftv.render import (
    human_bytes,
    render_card_header,
    render_report,
    render_scan_list,
    report_to_dict,
)
from ftv.session import run_session
from ftv.volumes import MacOSVolumeDetector, Volume

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


def _resolve_db_path(db: Path | None) -> Path:
    return (db or default_db_path()).expanduser().resolve()


def _require_existing_db(db: Path | None) -> None:
    """Refuse to proceed if the index database has not been created yet.

    Read/update-oriented commands (``scan list``, ``scan refresh``, ``scan
    rm``, ``verify``) must never silently create a new, empty index database
    (and its parent directory) just because ``--db`` pointed somewhere that
    does not exist yet — only ``scan add`` legitimately creates the index.
    """
    resolved = _resolve_db_path(db)
    if not resolved.is_file():
        err_console.print(
            f"[red]no index database found at {escape(str(resolved))}; "
            "run 'ftv scan add' first[/red]"
        )
        raise typer.Exit(code=2)


def _open_index(db: Path | None, roots: list[Path]) -> Index:
    try:
        return Index.open(db or default_db_path(), roots=roots)
    except DbPathError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=2) from exc
    except (OSError, sqlite3.Error) as exc:
        err_console.print(f"[red]could not access the index database: {escape(str(exc))}[/red]")
        raise typer.Exit(code=2) from exc


def _guard_index(fn, *args, **kwargs):
    """Run an Index method call, turning OSError/sqlite3.Error into a clean exit."""
    try:
        return fn(*args, **kwargs)
    except (OSError, sqlite3.Error) as exc:
        err_console.print(f"[red]could not access the index database: {escape(str(exc))}[/red]")
        raise typer.Exit(code=2) from exc


def _require_scan(index: Index, name: str):
    scan = _guard_index(index.get_scan, name)
    if scan is None:
        err_console.print(f"[red]no scan named '{escape(name)}'[/red]")
        available = [s.name for s in _guard_index(index.list_scans)]
        if available:
            err_console.print("Available scans: " + ", ".join(escape(n) for n in available))
        raise typer.Exit(code=2)
    return scan


def _report_walk_problems(outcome) -> None:
    if outcome.errors:
        console.print(
            f"[yellow]{len(outcome.errors):,} unreadable path"
            f"{'s' if len(outcome.errors) != 1 else ''} during the scan:[/yellow]"
        )
        for error in outcome.errors[:20]:
            console.print(f"  {escape(str(error.path))}  [dim]{escape(error.message)}[/dim]")
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
        err_console.print("[red]not a readable folder: " + escape(", ".join(missing)) + "[/red]")
        raise typer.Exit(code=2)

    skips = normalize_skip_list(skip)
    index = _open_index(db, roots)
    try:
        with console.status(f"Scanning {len(roots)} folder(s)…"):
            outcome = walk_trees(roots, skips)
        scan = _guard_index(
            index.save_scan,
            name,
            roots,
            outcome.entries,
            use_mtime=use_mtime,
            skip_extensions=skips,
        )
    except ScanExistsError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=2) from exc
    finally:
        index.close()

    console.print(
        f"[green]Scanned[/green] [bold]{escape(scan.name)}[/bold]: {scan.file_count:,} files "
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
    _require_existing_db(db)
    index = _open_index(db, [])
    try:
        scans = _guard_index(index.list_scans)
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
    _require_existing_db(db)
    index = _open_index(db, [])
    try:
        scan = _require_scan(index, name)
        try:
            check_destination_available(scan)
        except DestinationUnavailableError as exc:
            err_console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(code=2) from exc

        try:
            validate_db_path(_resolve_db_path(db), scan.roots)
        except DbPathError as exc:
            err_console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(code=2) from exc

        with console.status(f"Re-scanning {escape(scan.name)}…"):
            outcome = walk_trees(scan.roots, scan.skip_extensions)
        refreshed = _guard_index(
            index.save_scan,
            scan.name,
            scan.roots,
            outcome.entries,
            use_mtime=scan.use_mtime,
            skip_extensions=scan.skip_extensions,
            replace=True,
        )
    except ScanNotFoundError as exc:
        err_console.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=2) from exc
    finally:
        index.close()

    console.print(
        f"[green]Refreshed[/green] [bold]{escape(refreshed.name)}[/bold]: "
        f"{refreshed.file_count:,} files"
    )
    _report_walk_problems(outcome)


@scan_app.command("rm")
def scan_rm(
    name: Annotated[str, typer.Argument(help="The scan to delete.")],
    db: DbOption = None,
) -> None:
    """Forget a scan. Touches nothing on the destination."""
    _require_existing_db(db)
    index = _open_index(db, [])
    try:
        if not _guard_index(index.delete_scan, name):
            err_console.print(f"[red]no scan named '{escape(name)}'[/red]")
            raise typer.Exit(code=2)
    finally:
        index.close()

    console.print(f"[green]Deleted[/green] scan [bold]{escape(name)}[/bold]")


@app.command("verify")
def verify(
    scan_name: Annotated[str, typer.Option("--scan", help="The remembered scan to check against.")],
    db: DbOption = None,
    path: Annotated[
        Path | None,
        typer.Option("--path", help="Verify this folder once and exit, skipping detection."),
    ] = None,
    deep: Annotated[
        bool, typer.Option("--deep", help="Content-verify the suspicious pairs.")
    ] = False,
    deep_all: Annotated[
        bool, typer.Option("--deep-all", help="Content-verify every match.")
    ] = False,
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
    scope = DeepScope.ALL if deep_all else (DeepScope.SUSPICIOUS if deep else DeepScope.NONE)

    skip_override = normalize_skip_list(skip) if skip else None

    _require_existing_db(db)
    index = _open_index(db, [])
    try:
        scan = _require_scan(index, scan_name)

        try:
            check_destination_available(scan)
        except DestinationUnavailableError as exc:
            err_console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(code=2) from exc

        if rescan:
            with console.status(f"Re-scanning {escape(scan.name)}…"):
                outcome = walk_trees(scan.roots, scan.skip_extensions)
            scan = _guard_index(
                index.save_scan,
                scan.name,
                scan.roots,
                outcome.entries,
                use_mtime=scan.use_mtime,
                skip_extensions=scan.skip_extensions,
                replace=True,
            )

        lookup = _guard_index(index.lookup_for, scan.name)

        if path is not None:
            card_root = path.expanduser().resolve()
            if not card_root.is_dir():
                err_console.print(f"[red]not a readable folder: {escape(str(card_root))}[/red]")
                raise typer.Exit(code=2)

            report = verify_card(card_root, scan, lookup, deep=scope, skip_override=skip_override)
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
