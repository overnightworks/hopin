"""Tests for the merge of Vitest's and the browser's lcov reports."""

from typing import TYPE_CHECKING

import pytest

from scripts import merge_frontend_coverage

if TYPE_CHECKING:
    from pathlib import Path


def record(source: str, lines_hit: int, lines_found: int = 4) -> str:
    return f"TN:\nSF:{source}\nDA:1,{lines_hit}\nLF:{lines_found}\nLH:{lines_hit}\nend_of_record\n"


def merged_text(tmp_path: Path, vitest: str, browser: str) -> str:
    vitest_report, browser_report, merged_report = (tmp_path / name for name in ("v.info", "b.info", "m.info"))
    vitest_report.write_text(vitest)
    browser_report.write_text(browser)
    assert merge_frontend_coverage.main([str(vitest_report), str(browser_report), str(merged_report)]) == 0
    return merged_report.read_text()


def test_main_vitest_record_is_kept_byte_for_byte(tmp_path: Path) -> None:
    vitest = record("frontend/src/lib/a.ts", 4)

    assert merged_text(tmp_path, vitest, "").startswith(vitest)


def test_main_browser_record_for_a_vitest_file_is_dropped(tmp_path: Path) -> None:
    vitest = record("frontend/src/lib/a.ts", 4)

    assert merged_text(tmp_path, vitest, record("frontend/src/lib/a.ts", 2)) == vitest


def test_main_browser_record_for_another_file_is_kept(tmp_path: Path) -> None:
    vitest = record("frontend/src/lib/a.ts", 4)
    browser = record("frontend/src/lib/legacy/app.js", 3)

    assert merged_text(tmp_path, vitest, browser) == vitest + browser


@pytest.mark.parametrize("foreign_report", ["vitest", "browser"])
def test_main_source_outside_frontend_src_exits_naming_the_path(tmp_path: Path, foreign_report: str) -> None:
    reports = {"vitest": record("frontend/src/lib/a.ts", 4), "browser": ""}
    reports[foreign_report] = record("src/lib/a.ts", 4)

    with pytest.raises(SystemExit, match=r"src/lib/a\.ts"):
        merged_text(tmp_path, reports["vitest"], reports["browser"])


def test_main_missing_report_exits_naming_it(tmp_path: Path) -> None:
    present = tmp_path / "present.info"
    present.write_text("")
    missing = tmp_path / "missing.info"

    with pytest.raises(SystemExit, match=r"missing\.info"):
        merge_frontend_coverage.main([str(present), str(missing), str(tmp_path / "out.info")])


def test_main_prints_files_below_full_coverage_and_totals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    merged_text(tmp_path, record("frontend/src/lib/a.ts", 3), record("frontend/src/lib/b.js", 4))

    printed = capsys.readouterr().out
    assert "frontend/src/lib/a.ts: lines 3/4" in printed
    assert "b.js" not in printed
    assert "total lines: 7/8" in printed
