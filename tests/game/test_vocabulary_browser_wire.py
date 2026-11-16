import re
from typing import TYPE_CHECKING, get_args

import pytest

from hopin.game.frames import (
    BROWSER_FRAMES,
    AnswerCountFrame,
    BusyRefusedFrame,
    EndedFrame,
    GameFrame,
    IdlePhaseFrame,
    LineFrame,
    OtherRefusal,
    QuizPhaseFrame,
    RefusedFrame,
    SilentVoiceFrame,
    VoiceFrame,
)
from hopin.game.vocabulary import BROWSER_ENUMS, FEATURE_NAME, GamePhase, GameRefusal
from hopin.signaling.frames import PeerId
from scripts import generate_wire
from tests.support.rendered_frames import rendered_property_names

if TYPE_CHECKING:
    from pathlib import Path


def game_output(root: Path) -> Path:
    [output] = [path for path in generate_wire.outputs(root) if path.parent.name == FEATURE_NAME]
    return output


def test_the_generator_writes_the_games_enums_beside_the_game_from_their_owners(tmp_path: Path) -> None:
    generate_wire.main([], tmp_path)

    written = game_output(tmp_path).read_text()
    for exported_name, vocabulary in BROWSER_ENUMS.items():
        assert f"export enum {exported_name} {{" in written
        for name, member in vocabulary.__members__.items():
            assert f'    {name} = "{member.value}",' in written


def test_the_check_fails_when_the_games_file_drifts(tmp_path: Path) -> None:
    generate_wire.main([], tmp_path)
    output = game_output(tmp_path)
    output.write_text(output.read_text() + "// hand edit\n")

    with pytest.raises(SystemExit, match="out of date"):
        generate_wire.main(["--check"], tmp_path)


FULLY_SET_FRAMES = [
    IdlePhaseFrame(GamePhase.IDLE),
    QuizPhaseFrame(GamePhase.ANSWERING, round=1, rounds=3, starter=PeerId("p1")),
    LineFrame("text", voiced=True, round=1),
    VoiceFrame("id", "path"),
    SilentVoiceFrame(),
    AnswerCountFrame(answered=1, players=2, own_answer_counted=True),
    BusyRefusedFrame(GameRefusal.BUSY, retry_after_seconds=5),
    RefusedFrame(GameRefusal.NO_ACCESS),
    EndedFrame(leaver="Ana"),
]


def test_every_browser_frame_has_a_fully_set_instance_here() -> None:
    assert {type(frame) for frame in FULLY_SET_FRAMES} == set(BROWSER_FRAMES)


@pytest.mark.parametrize("frame", FULLY_SET_FRAMES, ids=lambda frame: type(frame).__name__)
def test_a_frames_rendered_properties_are_the_keys_it_sends_in_order(frame: GameFrame) -> None:
    assert rendered_property_names(frame, BROWSER_ENUMS) == list(frame.to_json())


def test_the_signal_map_has_exactly_the_signals_the_frames_carry(tmp_path: Path) -> None:
    generate_wire.main([], tmp_path)

    written = game_output(tmp_path).read_text()
    mapped = set(re.findall(r"^    \[GAME_SIGNAL\.(\w+)\]:", written, flags=re.MULTILINE))
    assert mapped == {frame.TYPE.name for frame in BROWSER_FRAMES}


def test_the_refusals_without_a_wait_are_every_refusal_but_busy() -> None:
    assert set(get_args(OtherRefusal)) == set(GameRefusal) - {GameRefusal.BUSY}
