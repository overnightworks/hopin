import pytest
from agents.figure_vocabulary import FigureGesture as AgentGesture

from hopin.participation.vocabulary import FigureGesture


@pytest.mark.parametrize("gesture", AgentGesture, ids=lambda gesture: gesture.value)
def test_every_gesture_an_agent_can_name_is_one_participation_tells_a_seat(gesture: AgentGesture) -> None:
    assert FigureGesture(gesture.value)
