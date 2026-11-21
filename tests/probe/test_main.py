import sys
from typing import TYPE_CHECKING

import pytest

from hopin_probe import main as probe
from tests.probe.conftest import NOT_REACHED, SENTINEL

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def restore_the_excepthook(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` installs its own excepthook; the one the suite runs with comes back after each test."""
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)


def run(capsys: pytest.CaptureFixture[str]) -> tuple[int, list[str], str]:
    exit_code = probe.main()
    captured = capsys.readouterr()
    return exit_code, captured.out.splitlines(), captured.out + captured.err


@pytest.mark.parametrize(
    ("setting", "value", "field"),
    [
        pytest.param("ORIGIN", None, "origin", id="origin-missing"),
        pytest.param("ORIGIN", "not a url", "origin", id="origin-not-a-url"),
        pytest.param("USERNAME", None, "username", id="username-missing"),
        pytest.param("AGENTS", "[]", "agents", id="no-agent"),
        pytest.param("AGENTS", f"{SENTINEL}", "agents", id="agents-not-json"),
        pytest.param("JOIN_SECONDS", "0", "join_seconds", id="no-time-to-join"),
    ],
)
def test_a_setting_that_fails_validation_is_named_by_its_field_and_never_echoed(
    probe_environment: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    setting: str,
    value: str | None,
    field: str,
) -> None:
    if value is None:
        monkeypatch.delenv(f"HOPIN_PROBE_{setting}")
    else:
        monkeypatch.setenv(f"HOPIN_PROBE_{setting}", value)

    exit_code, lines, everything = run(capsys)

    assert exit_code == 2
    assert lines == [f"stage=settings verdict=failed reason=invalid field={field}", *NOT_REACHED]
    assert value is None or value not in everything


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        pytest.param(None, "password_file_missing", id="missing"),
        pytest.param(" \n", "password_file_empty", id="empty"),
    ],
)
def test_a_password_file_that_is_missing_or_empty_stops_the_run_in_the_settings_stage(
    probe_environment: Path, capsys: pytest.CaptureFixture[str], content: str | None, reason: str
) -> None:
    if content is None:
        probe_environment.unlink()
    else:
        probe_environment.write_text(content)

    exit_code, lines, _ = run(capsys)

    assert exit_code == 2
    assert lines == [f"stage=settings verdict=failed reason={reason}", *NOT_REACHED]


def test_a_voice_that_is_not_there_stops_the_run_in_the_voice_stage(
    probe_environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code, lines, _ = run(capsys)

    assert exit_code == 2
    assert lines == ["stage=voice verdict=failed reason=voice_missing", *NOT_REACHED]


def test_an_unexpected_exception_outside_a_check_prints_its_stage_and_class_and_never_its_message(
    probe_environment: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def break_with_text(*_arguments: object) -> bytes:
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(probe, "synthesise", break_with_text)

    exit_code, lines, everything = run(capsys)

    assert exit_code == 2
    assert lines == ["stage=voice verdict=failed reason=error error=RuntimeError", *NOT_REACHED]
    assert SENTINEL not in everything


def test_what_escapes_the_run_is_reported_by_its_class_alone(
    probe_environment: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    probe.main()
    capsys.readouterr()

    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(SENTINEL), None)

    captured = capsys.readouterr()
    assert captured.out.splitlines() == ["verdict=failed reason=error error=KeyboardInterrupt"]
    assert SENTINEL not in captured.out + captured.err
