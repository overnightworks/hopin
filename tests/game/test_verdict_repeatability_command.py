"""`hopin verdict-repeatability` through its entry point, over a scripted provider instead of the configured one."""

import pytest

from hopin.game.quiz import Judgement
from hopin.game.verdict_repeatability import recorded_cases
from hopin.main import main
from tests.game.support.fake_agent_backend import FakeAgentBackend, Succeeds

pytestmark = pytest.mark.usefixtures("game_environment")


def verdicts_for_every_recorded_answer(judgement):
    return [
        Succeeds(
            "Auflösung.",
            verdicts=tuple({"nr": number, "judgement": judgement} for number in range(1, len(case.answers) + 1)),
        )
        for case in recorded_cases()
    ]


def run_command(backend, *arguments):
    with pytest.raises(SystemExit) as ended:
        main(["verdict-repeatability", "--runs", "1", *arguments], quiz_backend_of=lambda _settings: backend)
    return ended.value.code


def test_prints_the_report_and_exits_zero_when_the_verdicts_agree(capsys):
    backend = FakeAgentBackend()
    backend.answer_next(*verdicts_for_every_recorded_answer(Judgement.CORRECT.value))

    assert run_command(backend) == 0

    assert "1/1" in capsys.readouterr().out
    assert backend.closes == 1


def test_exits_one_when_a_run_gives_no_verdict(capsys):
    backend = FakeAgentBackend()
    backend.answer_next(*verdicts_for_every_recorded_answer("maybe"))

    assert run_command(backend) == 1

    assert "no verdict" in capsys.readouterr().out


def test_refuses_in_one_line_naming_the_missing_settings_when_the_agent_settings_are_absent(monkeypatch, capsys):
    monkeypatch.delenv("HOPIN_AGENT__GATEWAY__ORIGIN")
    backend = FakeAgentBackend()

    assert run_command(backend) == 1

    captured = capsys.readouterr()
    (line,) = captured.err.splitlines()
    assert "HOPIN_AGENT__GATEWAY__ORIGIN" in line
    assert captured.out == ""
    assert backend.runs == []


def test_parses_its_runs_and_minimum(capsys):
    with pytest.raises(SystemExit) as helped:
        main(["verdict-repeatability", "--help"])
    assert helped.value.code == 0
    help_text = capsys.readouterr().out
    assert "--runs" in help_text
    assert "--minimum" in help_text
