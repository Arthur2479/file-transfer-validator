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
