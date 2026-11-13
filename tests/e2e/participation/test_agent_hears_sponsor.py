from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from agents.member import STANDING_URI
from playwright.sync_api import Page, WebSocket, expect

from hopin.participation.vocabulary import HEARING_PATH
from tests.e2e.support.dialed_agent import AGENT_TILE, room_of_link
from tests.e2e.support.people import FORM_FACTORS
from tests.support.dialed_call import window_uri
from tests.support.fake_speech import a_sentence

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.participation_scene import Scene
    from tests.e2e.support.server import RunningServer
    from tests.support.fake_speech import FakeSpeech, Session

pytestmark = pytest.mark.e2e

SWITCH = AGENT_TILE + ' [role="switch"]'
MUTE = "#muteBtn"
SENTENCE = "Guten Tag"
WAKE_SECONDS = 20
HOP_SECONDS = 20
NO_HOP_WINDOW_MILLISECONDS = 1_000
POLL_MILLISECONDS = 50


@pytest.fixture
def e2e_server(speaking_server: RunningServer) -> RunningServer:
    return speaking_server


@dataclass
class Hop:
    """One hearing socket a page opened."""

    url: str
    closed: bool = False


@dataclass
class Hops:
    """The hearing sockets a page opens, as the browser reports them."""

    opened: list[Hop] = field(default_factory=list)

    def watch(self, page: Page) -> None:
        def noted(socket: WebSocket) -> None:
            if HEARING_PATH not in socket.url:
                return
            hop = Hop(socket.url)
            self.opened.append(hop)
            socket.on("close", lambda _socket: setattr(hop, "closed", True))

        page.on("websocket", noted)

    def open_ones(self) -> list[Hop]:
        return [hop for hop in self.opened if not hop.closed]


def until(page: Page, condition: Callable[[], bool], what: str) -> None:
    """Let the page run until `condition` holds; the page's events arrive while it waits."""
    for _ in range(HOP_SECONDS * 1000 // POLL_MILLISECONDS):
        if condition():
            return
        page.wait_for_timeout(POLL_MILLISECONDS)
    pytest.fail(f"{what} did not happen within {HOP_SECONDS} seconds")


def settle(page: Page) -> None:
    """A window in which a hop that should not open would have."""
    page.wait_for_timeout(NO_HOP_WINDOW_MILLISECONDS)


class Sponsor:
    """The owner of the agent a member added, whose switch starts off, and the hops the pages of the call open: the
    sponsor's, and each of the two other people's."""

    def __init__(self, scene: Scene, member_of: Callable[[Page], Any], speech: FakeSpeech) -> None:
        self.page = scene.owner
        self.agent = scene.agent
        self.hops = Hops()
        self.hops.watch(self.page)
        self.others_hops = [Hops(), Hops()]
        for other, hops in zip((scene.host, scene.member), self.others_hops, strict=True):
            hops.watch(other)
        self.name = member_of(self.page).account.username
        self.room = room_of_link(scene.link)
        self.speech = speech
        self.sessions_before = len(speech.sessions)
        scene.member_adds_and_the_agent_accepts()
        self.agent.listen([STANDING_URI, window_uri(self.room)])

    def press_the_switch(self) -> None:
        self.page.locator(SWITCH).click()

    def session(self, number: int) -> Session:
        return self.speech.next_session(self.sessions_before + number)

    def heard(self) -> list[tuple[str, str, str, bool]]:
        window = self.agent.read(window_uri(self.room))
        return [(line["kind"], line["name"], line["text"], line["spoken"]) for line in window["entries"]]


@pytest.fixture
def sponsor(
    scene_of: Callable[[dict[str, Any]], Scene],
    member_of: Callable[[Page], Any],
    fake_speech: FakeSpeech,
    form_factor: dict[str, Any],
) -> Sponsor:
    return Sponsor(scene_of(form_factor), member_of, fake_speech)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_sponsors_sentence_reaches_the_agent_and_no_one_else_opens_a_hop(sponsor: Sponsor) -> None:
    """CALL-59: While the switch is off the sponsor's browser opens no hop; with the switch and the microphone on it
    sends the microphone's sound and the agent reads the sentence the speech service finds in it as a spoken line of
    kind person under the sponsor's name; no other person, whose microphone is on too, opens a hop."""
    settle(sponsor.page)
    assert sponsor.hops.opened == []

    sponsor.press_the_switch()
    session = sponsor.session(1)
    session.wait_for_audio(1)
    for event in a_sentence(SENTENCE):
        session.say(event)

    assert sponsor.agent.woken_within(WAKE_SECONDS)
    assert sponsor.heard() == [("person", sponsor.name, SENTENCE, True)]
    until(sponsor.page, lambda: len(sponsor.hops.opened) == 1, "the sponsor's hop")
    settle(sponsor.page)
    assert [hops.opened for hops in sponsor.others_hops] == [[], []]
    assert len(sponsor.hops.opened) == 1


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_switch_and_the_microphone_each_end_the_sending_at_once_and_only_both_on_start_it(sponsor: Sponsor) -> None:
    """CALL-59: Turning the switch or the microphone off closes the hop, so the speech service gets no frame after
    it, and the microphone on again opens a new hop only while the switch is on."""
    page = sponsor.page
    sponsor.press_the_switch()
    first = sponsor.session(1)
    first.wait_for_audio(1)

    sponsor.press_the_switch()
    until(page, lambda: sponsor.hops.opened[0].closed, "the hop's close after the switch went off")
    first.wait_until_closed()
    after_the_close = first.audio_appends()
    page.click(MUTE)
    page.click(MUTE)
    settle(page)
    assert (first.audio_appends(), len(sponsor.hops.opened)) == (after_the_close, 1)

    sponsor.press_the_switch()
    until(page, lambda: len(sponsor.hops.opened) == 2, "a new hop after the switch went on")
    second = sponsor.session(2)
    second.wait_for_audio(1)

    page.click(MUTE)
    until(page, lambda: sponsor.hops.opened[1].closed, "the hop's close after the microphone went off")
    second.wait_until_closed()
    after_the_microphone = second.audio_appends()
    settle(page)
    assert (second.audio_appends(), len(sponsor.hops.opened)) == (after_the_microphone, 2)

    page.click(MUTE)
    until(page, lambda: len(sponsor.hops.opened) == 3, "a new hop after the microphone went on")
    sponsor.session(3).wait_for_audio(1)
    expect(page.locator(SWITCH)).to_have_attribute("aria-checked", "true")
