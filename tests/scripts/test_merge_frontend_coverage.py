"""Tests for the merge of Vitest's and the browser's lcov reports."""

from typing import TYPE_CHECKING

import pytest

from scripts import merge_frontend_coverage

if TYPE_CHECKING:
    from pathlib import Path


def record(source: str, lines_hit: int, lines_found: int = 4) -> str:
    return f"TN:\nSF:{source}\nDA:1,{lines_hit}\nLF:{lines_found}\nLH:{lines_hit}\nend_of_record\n"


def merge_reports(tmp_path: Path, vitest: str, browser: str) -> tuple[int, str]:
    vitest_report, browser_report, merged_report = (tmp_path / name for name in ("v.info", "b.info", "m.info"))
    vitest_report.write_text(vitest)
    browser_report.write_text(browser)
    status = merge_frontend_coverage.main([str(vitest_report), str(browser_report), str(merged_report)])
    return status, merged_report.read_text()


def merged_text(tmp_path: Path, vitest: str, browser: str) -> str:
    status, text = merge_reports(tmp_path, vitest, browser)
    assert status == 0
    return text


def test_main_vitest_record_is_kept_byte_for_byte(tmp_path: Path) -> None:
    vitest = record("frontend/src/lib/a.ts", 4)

    assert merged_text(tmp_path, vitest, "").startswith(vitest)


def test_main_browser_record_for_a_vitest_file_is_dropped(tmp_path: Path) -> None:
    vitest = record("frontend/src/lib/a.ts", 4)

    assert merged_text(tmp_path, vitest, record("frontend/src/lib/a.ts", 2)) == vitest


def test_main_browser_record_for_another_file_is_kept(tmp_path: Path) -> None:
    vitest = record("frontend/src/lib/a.ts", 4)
    browser = record("frontend/src/lib/b.ts", 4)

    assert merged_text(tmp_path, vitest, browser) == vitest + browser


@pytest.mark.parametrize("foreign_report", ["vitest", "browser"])
def test_main_source_outside_frontend_src_exits_naming_the_path(tmp_path: Path, foreign_report: str) -> None:
    reports = {"vitest": record("frontend/src/lib/a.ts", 4), "browser": ""}
    reports[foreign_report] = record("src/lib/a.ts", 4)

    with pytest.raises(SystemExit, match=r"src/lib/a\.ts"):
        merged_text(tmp_path, reports["vitest"], reports["browser"])


@pytest.mark.parametrize(
    ("report_text", "message"),
    [
        (record("frontend/src/lib/a.ts", 4) + "TN:\nSF:frontend/src/lib/b.ts\nLF:1\n", r"v\.info ends without"),
        (record("frontend/src/lib/a.ts", 4) + record("frontend/src/lib/a.ts", 2), r"v\.info names .*a\.ts twice"),
        ("TN:\nLF:1\nend_of_record\n", r"v\.info has a record without an SF:"),
    ],
    ids=["truncated", "duplicate-source", "missing-source"],
)
def test_main_malformed_report_exits_naming_it(tmp_path: Path, report_text: str, message: str) -> None:
    with pytest.raises(SystemExit, match=message):
        merged_text(tmp_path, report_text, "")


def test_main_missing_report_exits_naming_it(tmp_path: Path) -> None:
    present = tmp_path / "present.info"
    present.write_text("")
    missing = tmp_path / "missing.info"

    with pytest.raises(SystemExit, match=r"missing\.info"):
        merge_frontend_coverage.main([str(present), str(missing), str(tmp_path / "out.info")])


def test_main_prints_files_below_full_coverage_and_totals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    uncovered = "TN:\nSF:frontend/src/lib/a.ts\nDA:1,0\nDA:2,1\nDA:3,1\nLF:4\nLH:3\nend_of_record\n"
    merge_reports(tmp_path, uncovered, record("frontend/src/lib/b.js", 4))

    printed = capsys.readouterr().out
    assert "frontend/src/lib/a.ts: lines 3/4" in printed
    assert "b.js" not in printed
    assert "total lines: 7/8" in printed


@pytest.mark.parametrize(
    ("counted_lines", "missed"),
    [
        ("", None),
        ("DA:7,0\nLF:1\nLH:0", "line 7"),
        ("BRDA:9,0,1,0\nBRF:1\nBRH:0", "branch 9:0.1"),
        ("FNDA:0,onClick\nFNF:1\nFNH:0", "function onClick"),
        ("DA:7,0", "line 7"),
        ("DA:7,0\nLF:1\nLH:1", "line 7"),
    ],
    ids=["full", "missed-line", "missed-branch", "missed-function", "no-counters", "counters-claim-full"],
)
def test_main_report_below_full_coverage_exits_non_zero_naming_the_file_and_the_miss(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], counted_lines: str, missed: str | None
) -> None:
    source = "frontend/src/lib/a.ts"
    browser = f"TN:\nSF:{source}\n{counted_lines}\nend_of_record\n"

    status, _ = merge_reports(tmp_path, "", browser)

    assert status == (0 if missed is None else 1)
    if missed is not None:
        assert f"{source}: missed {missed}" in capsys.readouterr().out
