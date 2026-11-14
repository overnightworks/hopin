import base64
import json
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect

from hopin.participation.jpeg import size_of
from hopin.participation.vocabulary import LOOK_MAX_PIXELS
from hopin.words import text
from tests.e2e.support.dialed_agent import AGENT_TILE, REQUEST_SECONDS, room_of_link
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.participation_scene import Scene

pytestmark = pytest.mark.e2e

SWITCH_NAME = text("de", "tile.seeSwitchLabel")
CAMERA_BUTTON = "#videoBtn"
LOOK_MARK = AGENT_TILE + " .look-mark"
JPEG_MEDIA_TYPE = "image/jpeg"


class Sponsor:
    """The owner of the agent a member added: the agent's see switch is theirs alone, and the agent looks at their
    camera."""

    def __init__(self, scene: Scene) -> None:
        self.scene = scene
        self.page = scene.owner
        self.room = room_of_link(scene.link)
        scene.member_adds_and_the_agent_accepts()
        expect(self.switch).to_be_visible()

    @property
    def switch(self) -> Any:
        return self.page.get_by_role("switch", name=SWITCH_NAME)

    def switch_on(self) -> None:
        self.switch.click()
        expect(self.switch).to_have_attribute("aria-checked", "true")

    def look(self) -> Any:
        return self.scene.agent.look_unawaited(self.room)


def refusal_of(answer: Any) -> str:
    assert answer.is_error
    return json.loads(answer.content[0].text)["refused"]


@pytest.fixture
def sponsor(scene_of: Callable[[dict[str, Any]], Scene], form_factor: dict[str, Any]) -> Sponsor:
    return Sponsor(scene_of(form_factor))


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_switch_starts_off_on_the_sponsors_tile_of_the_agent_and_no_other_page_has_it(
    sponsor: Sponsor,
) -> None:
    """CALL-62: The see switch starts off, though the agent was added by another member, and only the sponsor has it."""
    expect(sponsor.switch).to_have_attribute("aria-checked", "false")
    for other in (sponsor.scene.host, sponsor.scene.member):
        expect(other.locator(AGENT_TILE)).to_have_count(1)
        expect(other.get_by_role("switch", name=SWITCH_NAME)).to_have_count(0)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_see_switchs_visible_label_is_whole_on_the_sponsors_tile(sponsor: Sponsor) -> None:
    """CALL-62: The sponsor's consent control reads whole on a phone and on a desktop, its label never cut."""
    label = sponsor.switch.locator(".label")
    expect(label).to_be_visible()
    assert label.evaluate("element => element.scrollWidth <= element.clientWidth")


@pytest.mark.parametrize("form_factor", [DESKTOP])
def test_a_look_without_consent_is_refused_and_the_sponsors_camera_is_not_read(sponsor: Sponsor) -> None:
    """CALL-63: With the switch off the agent's look is refused as not_allowed and no page shows a mark."""
    answer = sponsor.look().result(REQUEST_SECONDS)

    assert refusal_of(answer) == "not_allowed"
    for page in sponsor.scene.everyone:
        expect(page.locator(LOOK_MARK)).to_have_count(0)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_look_returns_one_jpeg_of_the_sponsors_camera_and_marks_the_agents_tile_on_every_page(
    sponsor: Sponsor,
) -> None:
    """CALL-63: With the switch on, a look is one JPEG within 1280 pixels and no text, and the agent's tile shows the
    mark on the pages of all three people, which takes the mark away again."""
    sponsor.switch_on()

    answer = sponsor.look()
    for page in sponsor.scene.everyone:
        expect(page.locator(LOOK_MARK)).to_have_count(1)
    result = answer.result(REQUEST_SECONDS)

    assert not result.is_error
    [block] = result.content
    assert block.type == "image"
    assert block.mime_type == JPEG_MEDIA_TYPE
    width, height = size_of(base64.b64decode(block.data)) or (0, 0)
    assert 0 < max(width, height) <= LOOK_MAX_PIXELS
    for page in sponsor.scene.everyone:
        expect(page.locator(LOOK_MARK)).to_have_count(0)


@pytest.mark.parametrize("form_factor", [DESKTOP])
def test_turning_the_switch_off_again_refuses_the_next_look(sponsor: Sponsor) -> None:
    """CALL-62: The sponsor takes consent back with one press."""
    sponsor.switch_on()
    sponsor.switch.click()
    expect(sponsor.switch).to_have_attribute("aria-checked", "false")

    assert refusal_of(sponsor.look().result(REQUEST_SECONDS)) == "not_allowed"


@pytest.mark.parametrize("form_factor", [DESKTOP])
def test_a_look_while_the_sponsors_camera_is_off_is_refused_as_no_camera(sponsor: Sponsor) -> None:
    """CALL-63: The sponsor's browser answers that no camera is on, and no page shows a mark."""
    sponsor.switch_on()
    sponsor.page.click(CAMERA_BUTTON)
    expect(sponsor.page.locator(CAMERA_BUTTON)).to_have_attribute("aria-pressed", "true")

    answer = sponsor.look().result(REQUEST_SECONDS)

    assert refusal_of(answer) == "no_camera"
    expect(sponsor.page.locator(LOOK_MARK)).to_have_count(0)
