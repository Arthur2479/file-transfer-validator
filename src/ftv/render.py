"""Everything the tool prints.

Confining output here keeps the pipeline testable without a terminal, and keeps
the UI testable by asserting on recorded text rather than driving a TUI.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath

from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree

from ftv.core.models import Scan, VerifyReport
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
    tree = Tree(escape(str(report.card_root)))
    branches: dict[str, Tree] = {}

    for verdict in sorted(report.missing, key=lambda v: v.entry.relpath):
        parts = PurePosixPath(verdict.entry.relpath).parts
        node = tree
        prefix = ""
        for folder in parts[:-1]:
            prefix = f"{prefix}/{folder}"
            if prefix not in branches:
                branches[prefix] = node.add(f"[bold]{escape(folder)}/[/bold]")
            node = branches[prefix]
        node.add(f"{escape(parts[-1])}  [dim]{human_bytes(verdict.entry.size)}[/dim]")

    return tree


def render_card_header(
    console: Console, scan: Scan, volume: Volume, file_count: int | None = None
) -> None:
    counted = "" if file_count is None else f" · {file_count:,} files"
    console.print(
        f"[bold]Scan:[/bold] {escape(scan.name)}   "
        f"[dim]scanned {human_age(scan.scanned_at)} · {scan.file_count:,} files[/dim]"
    )
    console.print(
        f"[bold]Card:[/bold] {escape(volume.name)}   "
        f"[dim]{escape(str(volume.path))} · {human_bytes(volume.total_bytes)}{counted}[/dim]"
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
        f"[dim]scan {escape(report.scan_name)} · scanned {human_age(scan.scanned_at)}[/dim]"
    )

    if report.checked_count == 0 and not report.errors:
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
            console.print(
                f"     {escape(verdict.entry.relpath)}  [dim]{escape(verdict.reason)}[/dim]"
            )
            if verdict.matched is not None:
                console.print(f"       [dim]destination: {escape(str(verdict.matched.path))}[/dim]")
        console.print("   [dim]run again with --deep to settle these by content[/dim]")

    if report.errors:
        console.print(
            f"   [bold red]{len(report.errors):,} UNREADABLE[/bold red]"
            " — these could not be checked"
        )
        for error in report.errors:
            console.print(f"     {escape(str(error.path))}  [dim]{escape(error.message)}[/dim]")

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
            escape(scan.name),
            f"{scan.file_count:,}",
            human_age(scan.scanned_at),
            "on" if scan.use_mtime else "off",
            escape(", ".join(sorted(scan.skip_extensions))) or "—",
            escape("\n".join(str(root) for root in scan.roots)),
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
