from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, WebSocket, expect

from hopin.participation.vocabulary import HEARING_PATH
from tests.e2e.support.calls import open_call
from tests.e2e.support.dialed_agent import AGENT_TILE, room_of_link
from tests.e2e.support.people import FORM_FACTORS
from tests.support.dialed_call import window_uri
from tests.support.fake_speech import a_sentence

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.dialed_agent import OwnAgent
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
    """A person in a call whose own agent has joined and hears them, and the hops their page opens."""

    def __init__(
        self,
        open_person: Callable[..., Page],
        server: RunningServer,
        page_errors: list[str],
        member_of: Callable[[Page], Any],
        own_agent_of: Callable[[str], OwnAgent],
        speech: FakeSpeech,
        form_factor: dict[str, Any],
    ) -> None:
        self.page, self.guest, link = open_call(open_person, server.base_url, page_errors, form_factor)
        self.hops = Hops()
        self.hops.watch(self.page)
        self.guest_hops = Hops()
        self.guest_hops.watch(self.guest)
        self.name = member_of(self.page).account.username
        self.room = room_of_link(link)
        self.speech = speech
        self.sessions_before = len(speech.sessions)
        self.agent = own_agent_of(member_of(self.page).account.id)
        self.agent.dial_in(self.room, "Claude")
        self.agent.listen([window_uri(self.room)])

    def session(self, number: int) -> Session:
        return self.speech.next_session(self.sessions_before + number)

    def heard(self) -> list[tuple[str, str, str, bool]]:
        window = self.agent.read(window_uri(self.room))
        return [(line["kind"], line["name"], line["text"], line["spoken"]) for line in window["entries"]]


@pytest.fixture
def sponsor(
    open_person: Callable[..., Page],
    e2e_server: RunningServer,
    page_errors: list[str],
    member_of: Callable[[Page], Any],
    own_agent_of: Callable[[str], OwnAgent],
    fake_speech: FakeSpeech,
    form_factor: dict[str, Any],
) -> Sponsor:
    return Sponsor(open_person, e2e_server, page_errors, member_of, own_agent_of, fake_speech, form_factor)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_sponsors_sentence_reaches_the_agent_and_a_guest_opens_no_hop(sponsor: Sponsor) -> None:
    """CALL-59: With the switch and the microphone on, the sponsor's browser sends the microphone's sound and the
    agent reads the sentence the speech service finds in it as a spoken line of kind person under the sponsor's name;
    a guest, whose microphone is on too, opens no hop."""
    session = sponsor.session(1)
    session.wait_for_audio(1)
    for event in a_sentence(SENTENCE):
        session.say(event)

    assert sponsor.agent.woken_within(WAKE_SECONDS)
    assert sponsor.heard() == [("person", sponsor.name, SENTENCE, True)]
    assert sponsor.guest_hops.opened == []
    assert len(sponsor.hops.opened) == 1


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_switch_and_the_microphone_each_end_the_sending_at_once_and_only_both_on_start_it(sponsor: Sponsor) -> None:
    """CALL-59: Turning the switch or the microphone off closes the hop, so the speech service gets no frame after
    it, and the microphone on again opens a new hop only while the switch is on."""
    page = sponsor.page
    first = sponsor.session(1)
    first.wait_for_audio(1)

    page.locator(SWITCH).click()
    until(page, lambda: sponsor.hops.opened[0].closed, "the hop's close after the switch went off")
    first.wait_until_closed()
    after_the_close = first.audio_appends()
    page.click(MUTE)
    page.click(MUTE)
    settle(page)
    assert (first.audio_appends(), len(sponsor.hops.opened)) == (after_the_close, 1)

    page.locator(SWITCH).click()
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
