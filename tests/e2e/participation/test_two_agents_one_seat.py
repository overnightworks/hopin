import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from design.words import text as kit_text
from playwright.sync_api import Locator, Page, expect

from hopin.agent.tools import LEAVE_CALL
from hopin.participation.vocabulary import AgentRefusal
from hopin.words import text
from tests.e2e.support.calls import join_by_link, open_more
from tests.e2e.support.dialed_agent import AGENT_TILE, OWNER_SUBJECT, OwnAccountAgent, room_of_link
from tests.e2e.support.hearing_hops import Hops, settle, until
from tests.e2e.support.people import FORM_FACTORS
from tests.support.dialed_call import window_uri
from tests.support.fake_speech import a_sentence

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.participation_scene import Scene
    from tests.e2e.support.server import RunningServer
    from tests.support.fake_speech import FakeSpeech, Session

pytestmark = pytest.mark.e2e

OWN_SWITCH = '[role="switch"]'
SENTENCE = "Guten Tag"
LATER_SENTENCE = "Auf Wiedersehen"
THIRD_AGENT_NAME = "Raphael"
# A sentence counts for the agents whose switch was on when it began, so it begins after the second switch went on.
AFTER_BOTH_SWITCHES_MILLISECONDS = 5_000


@pytest.fixture
def e2e_server(speaking_server: RunningServer) -> RunningServer:
    return speaking_server


@dataclass
class Seat:
    """An owner's seat in a call whose scene's adder added both of the owner's agents: a member, with each agent's
    switch off, or the owner from their own seat, with each switch on."""

    scene: Scene
    first: OwnAccountAgent
    second: OwnAccountAgent
    speech: FakeSpeech

    def __post_init__(self) -> None:
        self.page = self.scene.owner
        self.hops = Hops()
        self.hops.watch(self.page)
        self.room = room_of_link(self.scene.link)
        self.sessions_before = len(self.speech.sessions)
        self.scene.member_adds_and_accepts(self.first)
        self.scene.member_adds_and_accepts(self.second)

    def tile_of(self, page: Page, agent: OwnAccountAgent) -> Locator:
        return page.locator(AGENT_TILE).filter(has_text=agent.name)

    def switch_of(self, agent: OwnAccountAgent) -> Locator:
        return self.tile_of(self.page, agent).locator(OWN_SWITCH)

    def switch(self, agent: OwnAccountAgent, *, on: bool) -> None:
        self.switch_of(agent).click()
        expect(self.switch_of(agent)).to_have_attribute("aria-checked", "true" if on else "false")

    def hearing_session(self) -> Session:
        """The speech session of the one hop the owner's page holds open, once it sends sound."""
        until(self.page, lambda: len(self.hops.open_ones()) == 1, "exactly one open hop")
        until(self.page, lambda: self.speech.sessions[-1].audio_appends() > 0, "sound at the speech service")
        session = self.speech.sessions[-1]
        assert not session.closed.is_set()
        return session

    def says(self, text: str) -> None:
        session = self.hearing_session()
        for event in a_sentence(
            text, started_ms=AFTER_BOTH_SWITCHES_MILLISECONDS, stopped_ms=AFTER_BOTH_SWITCHES_MILLISECONDS + 900
        ):
            session.say(event)

    def heard_by(self, agent: OwnAccountAgent) -> list[str]:
        window = agent.read(window_uri(self.room))
        return [line["text"] for line in window["entries"] if line["spoken"]]


@pytest.fixture
def seat_of(
    scene_of: Callable[..., Scene],
    own_account_agent: OwnAccountAgent,
    second_own_account_agent: OwnAccountAgent,
    fake_speech: FakeSpeech,
    form_factor: dict[str, Any],
) -> Callable[..., Seat]:
    """Builds the seat of a member who adds both agents, or with `owner_adds` the owner from their own seat."""

    def build(*, owner_adds: bool = False) -> Seat:
        scene = scene_of(form_factor, owner_adds=owner_adds)
        return Seat(scene, own_account_agent, second_own_account_agent, fake_speech)

    return build


def wait_for_sentence(seat: Seat, agent: OwnAccountAgent, text: str) -> None:
    until(seat.page, lambda: text in seat.heard_by(agent), f"{text!r} in {agent.name}'s window")


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_seat_brings_both_of_its_owners_agents_and_each_hears_only_while_its_own_switch_is_on(
    seat_of: Callable[..., Seat],
) -> None:
    """CALL-51, CALL-57: The owner's seat holds both agents of the owner in one call, each tile in every browser;
    with both switches on the sound reaches both, with one off only the other, and with both off the owner's browser
    sends nothing."""
    seat = seat_of()
    for person in seat.scene.everyone:
        expect(person.locator(AGENT_TILE)).to_have_count(2)
    for agent in (seat.first, seat.second):
        expect(seat.switch_of(agent)).to_have_attribute("aria-checked", "false")
    settle(seat.page)
    assert seat.hops.opened == []

    seat.switch(seat.first, on=True)
    seat.switch(seat.second, on=True)
    seat.says(SENTENCE)
    for agent in (seat.first, seat.second):
        wait_for_sentence(seat, agent, SENTENCE)

    seat.switch(seat.second, on=False)
    settle(seat.page)
    seat.says(LATER_SENTENCE)
    wait_for_sentence(seat, seat.first, LATER_SENTENCE)
    settle(seat.page)
    assert seat.heard_by(seat.second) == [SENTENCE]

    seat.switch(seat.first, on=False)
    until(seat.page, lambda: seat.hops.open_ones() == [], "no open hop once both are off")
    sessions = list(seat.speech.sessions[seat.sessions_before :])
    sent = [session.audio_appends() for session in sessions]
    settle(seat.page)
    assert [session.audio_appends() for session in sessions] == sent


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_an_owner_adds_both_of_their_agents_from_their_own_seat_and_both_hear_at_once(
    seat_of: Callable[..., Seat],
) -> None:
    """CALL-51, CALL-57: After the first agent joined, the owner's seat still offers the second, which they add too;
    every browser shows both tiles, and as the owner added them the sound reaches both with no switch pressed."""
    seat = seat_of(owner_adds=True)
    for person in seat.scene.everyone:
        expect(person.locator(AGENT_TILE)).to_have_count(2)
    for agent in (seat.first, seat.second):
        expect(seat.switch_of(agent)).to_have_attribute("aria-checked", "true")

    seat.says(SENTENCE)

    for agent in (seat.first, seat.second):
        wait_for_sentence(seat, agent, SENTENCE)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_the_other_agent_keeps_hearing_when_one_leaves(seat_of: Callable[..., Seat]) -> None:
    """CALL-57: When one of the two agents leaves, the other keeps hearing the owner."""
    seat = seat_of()
    seat.switch(seat.first, on=True)
    seat.switch(seat.second, on=True)
    seat.says(SENTENCE)
    for agent in (seat.first, seat.second):
        wait_for_sentence(seat, agent, SENTENCE)

    seat.first.call(LEAVE_CALL, {"room": seat.room})
    expect(seat.tile_of(seat.page, seat.first)).to_have_count(0)
    settle(seat.page)
    seat.says(LATER_SENTENCE)

    wait_for_sentence(seat, seat.second, LATER_SENTENCE)
    expect(seat.tile_of(seat.page, seat.second)).to_have_count(1)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_full_call_refuses_an_agent_and_a_person_and_a_place_an_agent_leaves_admits_the_third_agent(
    scene_of: Callable[[dict[str, Any]], Scene],
    open_person: Callable[..., Page],
    own_account_agent: OwnAccountAgent,
    second_own_account_agent: OwnAccountAgent,
    e2e_server: RunningServer,
    form_factor: dict[str, Any],
) -> None:
    """CALL-47, CALL-51: Agents take places like people: a fourth person fills the last place, the invited third
    agent's acceptance is refused as place full and the control says so, a further person is refused as full, and
    once an agent leaves the third agent's acceptance joins it."""
    scene = scene_of(form_factor)
    third = OwnAccountAgent(e2e_server, OWNER_SUBJECT, f"{THIRD_AGENT_NAME}-{secrets.token_hex(3)}")
    try:
        for agent in (own_account_agent, second_own_account_agent):
            scene.member_adds_and_accepts(agent)
        scene.member_adds(third)
        [invited] = [standing for standing in third.standings() if standing["state"] == "invited"]
        join_by_link(open_person(**form_factor), scene.link)

        assert third.accept_refusal(invited) == AgentRefusal.BUSY.value
        expect(open_more(scene.adder)).to_contain_text(kit_text("de", "agent.placeFull"))
        refused = open_person(**form_factor)
        join_by_link(refused, scene.link)
        expect(refused.locator("body")).to_contain_text(text("de", "end.fullTitle"))
        for person in scene.everyone:
            expect(person.locator(AGENT_TILE)).to_have_count(2)

        own_account_agent.call(LEAVE_CALL, {"room": room_of_link(scene.link)})
        third.accept(invited)

        for person in scene.everyone:
            expect(person.locator(AGENT_TILE).filter(has_text=third.name)).to_have_count(1)
    finally:
        third.close()
