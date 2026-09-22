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
    return FileEntry(
        path=Path("/Volumes/CARD") / relpath, relpath=relpath, name=name, size=size, mtime=1000.0
    )


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
            Verdict(
                entry=entry("DSC_0412.MOV", "DCIM/100NZ_8/DSC_0412.MOV", 2_100_000_000),
                bucket=Bucket.MISSING,
            ),
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
            Verdict(
                entry=entry("DSC_0412.MOV", "DCIM/100NZ_8/DSC_0412.MOV"), bucket=Bucket.MISSING
            ),
            Verdict(
                entry=entry("DSC_0413.MOV", "DCIM/100NZ_8/DSC_0413.MOV"), bucket=Bucket.MISSING
            ),
            Verdict(
                entry=entry("C0007.MP4", "PRIVATE/M4ROOT/CLIP/C0007.MP4"), bucket=Bucket.MISSING
            ),
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
                matched=IndexedFile(
                    path="/Volumes/SSD/april/DSC_0001.NEF", name="DSC_0001.NEF", size=10, mtime=1.0
                ),
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


def test_a_card_that_is_entirely_skip_listed_is_not_reported_as_all_present():
    """total_files > 0 (via skipped_user) but checked_count == 0 must still be neutral."""
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        skipped_user=("DCIM/A.NEF", "DCIM/B.NEF"),
    )
    c = console()
    render_report(c, report, scan())
    out = text(c)

    assert "no files to verify" in out.lower()
    assert not ("ALL" in out and "PRESENT" in out)
    assert report.exit_code == 0


def test_a_card_that_is_entirely_junk_is_not_reported_as_all_present():
    """total_files > 0 (via skipped_junk) but checked_count == 0 must still be neutral."""
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        skipped_junk=(".DS_Store", "Thumbs.db"),
    )
    c = console()
    render_report(c, report, scan())
    out = text(c)

    assert "no files to verify" in out.lower()
    assert not ("ALL" in out and "PRESENT" in out)
    assert report.exit_code == 0


def test_a_card_with_only_errors_still_reports_the_errors_not_the_neutral_message():
    """checked_count == 0 but errors present must still block, not go neutral."""
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        errors=(WalkError(path=Path("/Volumes/CARD/bad"), message="permission denied"),),
    )
    c = console()
    render_report(c, report, scan())
    out = text(c)

    assert "no files to verify" not in out.lower()
    assert "permission denied" in out


def test_the_no_mtime_suggestion_is_shown_when_asked_for():
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        verdicts=tuple(
            Verdict(
                entry=entry(f"IMG_{n}.JPG", f"DCIM/IMG_{n}.JPG"),
                bucket=Bucket.SUSPICIOUS,
                reason="same name and size but a different capture time",
            )
            for n in range(12)
        ),
    )
    c = console()
    render_report(c, report, scan(), suggest_no_mtime=True)
    out = text(c)

    assert "--no-mtime" in out
    assert "timestamp" in out.lower()


def test_a_bracketed_folder_name_is_not_swallowed_as_markup():
    """Filenames like "[edited]" look like Rich markup tags and must survive escaping."""
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        verdicts=(
            Verdict(
                entry=entry("photo.jpg", "DCIM/[edited]/photo.jpg"),
                bucket=Bucket.MISSING,
            ),
        ),
    )
    c = console()
    render_report(c, report, scan())
    out = text(c)

    assert "[edited]" in out
    assert "not" in out.lower() and "format" in out.lower()


def test_a_bracketed_folder_name_survives_in_the_missing_tree():
    report = VerifyReport(
        scan_name="S",
        card_root=Path("/Volumes/CARD"),
        verdicts=(
            Verdict(
                entry=entry("photo.jpg", "DCIM/[edited]/photo.jpg"),
                bucket=Bucket.MISSING,
            ),
        ),
    )
    c = console()
    c.print(build_missing_tree(report))
    out = text(c)

    assert "[edited]" in out


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
                matched=IndexedFile(
                    path="/Volumes/SSD/DSC_0001.NEF", name="DSC_0001.NEF", size=10, mtime=1.0
                ),
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
