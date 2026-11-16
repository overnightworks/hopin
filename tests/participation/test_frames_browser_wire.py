import re
from typing import TYPE_CHECKING

import pytest

from hopin.participation.frames import (
    BROWSER_FRAMES,
    AgentGestureFrame,
    AgentHearingFrame,
    AgentInvitationEndedFrame,
    AgentJoinedFrame,
    AgentLeftFrame,
    AgentLineFrame,
    AgentLookedFrame,
    AgentLookRequestFrame,
    AgentOffersFrame,
    AgentRefusedFrame,
    AgentSeeingFrame,
    AgentStopFrame,
    AgentVoiceFrame,
    Offer,
    ParticipationFrame,
)
from hopin.participation.vocabulary import (
    BROWSER_ENUMS,
    AgentRefusal,
    FigureGesture,
    InvitationOutcome,
    ParticipationSignal,
)
from scripts import generate_wire
from tests.support.rendered_frames import rendered_property_names

if TYPE_CHECKING:
    from pathlib import Path

PARTICIPATION_OUTPUT = "frontend/src/lib/features/participation/wire.ts"

FULLY_SET_FRAMES = [
    AgentJoinedFrame("a1", "Mozart", "Ana", "p1", figure="/f", gestures={"wave": "/c"}),
    AgentLeftFrame("a1"),
    AgentHearingFrame("a1", on=True),
    AgentSeeingFrame("a1", on=False),
    AgentLookRequestFrame("r1"),
    AgentLookedFrame("a1"),
    AgentVoiceFrame("a1", "v1"),
    AgentStopFrame("a1"),
    AgentGestureFrame("a1", FigureGesture.WAVE),
    AgentOffersFrame(shown=True, full=False, offers=[Offer("k", "Bach", pending=True)]),
    AgentInvitationEndedFrame("k", InvitationOutcome.BUSY),
    AgentLineFrame("a1", "Mozart", "Guten Tag"),
    AgentRefusedFrame(AgentRefusal.GONE),
]


def test_every_browser_frame_has_a_fully_set_instance_here() -> None:
    assert {type(frame) for frame in FULLY_SET_FRAMES} == set(BROWSER_FRAMES)


@pytest.mark.parametrize("frame", FULLY_SET_FRAMES, ids=lambda frame: type(frame).__name__)
def test_a_frames_rendered_properties_are_the_keys_it_sends_in_order(frame: ParticipationFrame) -> None:
    assert rendered_property_names(frame, BROWSER_ENUMS) == list(frame.to_json())


def test_an_offers_rendered_properties_are_the_keys_it_sends_in_order() -> None:
    offer = Offer("k", "Bach", pending=True)
    rendered = generate_wire.render_record_type(Offer, BROWSER_ENUMS)
    properties = re.findall(r"^    (\w+)\??:", rendered, flags=re.MULTILINE)

    assert properties == list(offer.to_json())


def test_the_joined_frame_sends_no_figure_or_gestures_it_does_not_have() -> None:
    assert AgentJoinedFrame("a1", "Mozart", "Ana", "p1").to_json() == {
        "type": ParticipationSignal.AGENT_JOINED.value,
        "agent_id": "a1",
        "name": "Mozart",
        "sponsor": "Ana",
        "sponsor_id": "p1",
    }


def test_the_signal_map_has_exactly_the_signals_the_frames_carry(tmp_path: Path) -> None:
    written = generate_wire.outputs(tmp_path)[tmp_path / PARTICIPATION_OUTPUT]

    mapped = set(re.findall(r"^    \[PARTICIPATION_SIGNAL\.(\w+)\]:", written, flags=re.MULTILINE))
    assert mapped == {frame.TYPE.name for frame in BROWSER_FRAMES}
