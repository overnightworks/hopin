from typing import TYPE_CHECKING

from hopin.participation.frames import (
    BROWSER_FRAMES,
    AgentJoinedFrame,
)
from hopin.participation.vocabulary import (
    ParticipationSignal,
)
from tests.support.rendered_frames import mapped_signal_names

if TYPE_CHECKING:
    from pathlib import Path


def test_the_joined_frame_sends_no_figure_or_gestures_it_does_not_have() -> None:
    assert AgentJoinedFrame("a1", "Mozart", "Ana").to_json() == {
        "type": ParticipationSignal.AGENT_JOINED.value,
        "agent_id": "a1",
        "name": "Mozart",
        "sponsor": "Ana",
    }


def test_the_signal_map_has_exactly_the_signals_the_frames_carry(tmp_path: Path) -> None:
    assert mapped_signal_names(tmp_path, "frontend/src/lib/features/participation/wire.ts", "PARTICIPATION_SIGNAL") == {
        frame.TYPE.name for frame in BROWSER_FRAMES
    }
