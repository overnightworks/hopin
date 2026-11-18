from enum import StrEnum

import pytest
from agents.figure_vocabulary import FigureGesture as AgentGesture
from agents.figure_vocabulary import FigureMood as AgentMood

from hopin.participation.vocabulary import FigureGesture, FigureMood


@pytest.mark.parametrize(
    ("named", "told"),
    [(gesture, FigureGesture) for gesture in AgentGesture] + [(mood, FigureMood) for mood in AgentMood],
    ids=lambda value: value.value if isinstance(value, StrEnum) else value.__name__,
)
def test_every_gesture_and_mood_an_agent_can_name_is_one_participation_tells_a_seat(
    named: StrEnum, told: type[StrEnum]
) -> None:
    assert told(named.value)
