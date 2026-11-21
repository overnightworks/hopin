from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

from tests.e2e.support.dialed_agent import AGENT_LISTEN_SWITCH, AGENT_TILE, REMOVE
from tests.e2e.support.people import FORM_FACTORS
from tests.e2e.support.speaker import SPEAKING_TILE, Speaker

if TYPE_CHECKING:
    from playwright.sync_api import Locator

    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

SOUND_SECONDS = 1.0
LONGER_SOUND_SECONDS = 5.0
LATER_SOUND_SECONDS = 1.5
PLAYBACK_WAIT_MILLISECONDS = 15_000
TOLERANCE_PIXELS = 1


@pytest.fixture
def e2e_server(voicing_server: RunningServer) -> RunningServer:
    return voicing_server


def box_of(locator: Locator) -> dict[str, float]:
    box = locator.bounding_box()
    assert box is not None
    return box


def lies_inside(inner: dict[str, float], outer: dict[str, float]) -> bool:
    return (
        inner["x"] >= outer["x"] - TOLERANCE_PIXELS
        and inner["y"] >= outer["y"] - TOLERANCE_PIXELS
        and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + TOLERANCE_PIXELS
        and inner["y"] + inner["height"] <= outer["y"] + outer["height"] + TOLERANCE_PIXELS
    )


def share_area(first: dict[str, float], second: dict[str, float]) -> float:
    width = min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"])
    height = min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"])
    return max(width, 0) * max(height, 0)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_each_person_mutes_an_agents_voice_in_their_own_browser_alone(speaker: Speaker) -> None:
    """CALL-79: The listen switch is on every person's agent tile, wholly inside it and clear of the remove button and
    of the other switches; turning it off in one browser ends the agent's playing sound there at once and keeps the
    tile present while the other browsers still hear it, and nothing more plays there, while turning it on plays
    from the agent's next sentence on."""
    muting, *others = speaker.scene.everyone
    switches = [person.locator(AGENT_LISTEN_SWITCH) for person in speaker.scene.everyone]
    for person, switch in zip(speaker.scene.everyone, switches, strict=True):
        expect(switch).to_have_attribute("aria-checked", "true")
        tile_box = box_of(person.locator(AGENT_TILE))
        switch_box = box_of(switch)
        assert lies_inside(switch_box, tile_box)
        other_boxes = [box_of(other) for other in person.locator(f"{AGENT_TILE} >> role=switch").all()]
        for other_box in other_boxes:
            assert other_box == switch_box or share_area(switch_box, other_box) == 0
        for remove in person.locator(f"{AGENT_TILE} {REMOVE}").all():
            assert share_area(switch_box, box_of(remove)) == 0

    speaker.speak(LONGER_SOUND_SECONDS)
    expect(muting.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
    switches[0].click()

    expect(switches[0]).to_have_attribute("aria-checked", "false")
    expect(muting.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)
    for person in others:
        expect(person.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
    for person in others:
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE, timeout=PLAYBACK_WAIT_MILLISECONDS)

    speaker.speak(SOUND_SECONDS)
    for person in others:
        person.wait_for_function("() => window.soundsStarted.length === 2", timeout=PLAYBACK_WAIT_MILLISECONDS)
        expect(person.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE, timeout=PLAYBACK_WAIT_MILLISECONDS)
    assert speaker.started(muting) == [LONGER_SOUND_SECONDS]
    expect(muting.locator(AGENT_TILE)).not_to_have_class(SPEAKING_TILE)

    switches[0].click()
    speaker.speak(LATER_SOUND_SECONDS)

    expect(muting.locator(AGENT_TILE)).to_have_class(SPEAKING_TILE)
    assert speaker.started(muting) == [LONGER_SOUND_SECONDS, LATER_SOUND_SECONDS]
