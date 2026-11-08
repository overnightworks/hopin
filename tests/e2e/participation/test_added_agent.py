from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.participation.conftest import AGENT_NAME, OWNER_USERNAME
from tests.e2e.support.calls import call_link_of, join_by_link, open_more, press_start_call, record_page_errors
from tests.e2e.support.dialed_agent import AGENT_TILE, COUNT, REMOVE
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.dialed_agent import OwnAccountAgent
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

OFFER = "#agentOffers button"
LEAVE = "#leaveBtn"
INVITATION_WAIT_SECONDS = 20


class Scene:
    """The host, the agent's owner and a member of the adders group in one call, and the owner's scripted agent. The
    agent has called once, so it is registered; it holds no place yet."""

    def __init__(
        self,
        open_person: Callable[..., Page],
        member_of: Callable[[Page], Any],
        server: RunningServer,
        page_errors: list[str],
        form_factor: dict[str, Any],
        grant_adders: Callable[[str], None],
        agent: OwnAccountAgent,
    ):
        self.agent = agent
        self.host = record_page_errors(open_person(**form_factor), page_errors)
        press_start_call(self.host, server.base_url)
        self.link = call_link_of(self.host)
        self.owner = record_page_errors(open_person(username=OWNER_USERNAME, **form_factor), page_errors)
        join_by_link(self.owner, self.link)
        self.member = record_page_errors(open_person(**form_factor), page_errors)
        grant_adders(member_of(self.member).account.id)
        join_by_link(self.member, self.link)
        self.everyone = (self.host, self.owner, self.member)

    def agent_waits(self) -> None:
        """The agent's first call registers it, so a member's seat is offered it."""
        assert self.agent.standings() == []

    def member_adds_and_the_agent_accepts(self) -> None:
        self.agent_waits()
        open_more(self.member)
        offer = self.member.locator(OFFER)
        expect(offer).to_have_text(f"Add {AGENT_NAME}")
        offer.click()
        expect(offer).to_have_text(f"Inviting {AGENT_NAME}…")
        expect(offer).to_be_disabled()
        accepted = self.agent.accept(self.agent.invitation_within(INVITATION_WAIT_SECONDS))
        assert accepted["name"] == AGENT_NAME


@pytest.fixture
def scene_of(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], Any],
    e2e_server: RunningServer,
    page_errors: list[str],
    grant_adders: Callable[[str], None],
    own_account_agent: OwnAccountAgent,
) -> Callable[[dict[str, Any]], Scene]:
    def build(form_factor: dict[str, Any]) -> Scene:
        return Scene(open_person, member_of, e2e_server, page_errors, form_factor, grant_adders, own_account_agent)

    return build


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_member_adds_the_owners_agent_its_tile_shows_the_name_and_the_mark_and_the_host_removes_it(
    scene_of: Callable[[dict[str, Any]], Scene], form_factor: dict[str, Any]
) -> None:
    """CALL-51: A member of the adders group adds the owner's agent; it shows with its name, the AI mark and its
    sponsor, and the host removes it."""
    scene = scene_of(form_factor)

    scene.member_adds_and_the_agent_accepts()

    for person in scene.everyone:
        tile = person.locator(AGENT_TILE)
        expect(tile).to_have_count(1)
        expect(tile.locator(".name")).to_contain_text(AGENT_NAME)
        expect(tile.locator(".ai-badge")).to_be_visible()
        expect(tile.locator(".sponsor")).to_have_text(OWNER_USERNAME)
        expect(person.locator(COUNT)).to_have_text("3")
    expect(scene.member.locator(AGENT_TILE + " " + REMOVE)).to_have_count(0)
    assert [standing["state"] for standing in scene.agent.standings()] == ["active"]

    scene.host.locator(AGENT_TILE + " " + REMOVE).click()

    for person in scene.everyone:
        expect(person.locator(AGENT_TILE)).to_have_count(0)
    assert scene.agent.standings() == []


def test_the_agent_leaves_when_its_owner_leaves_for_good(scene_of: Callable[[dict[str, Any]], Scene]) -> None:
    """CALL-51: The agent leaves with its owner's seat."""
    scene = scene_of(DESKTOP)
    scene.member_adds_and_the_agent_accepts()
    expect(scene.host.locator(AGENT_TILE)).to_have_count(1)

    scene.owner.click(LEAVE)

    for person in (scene.host, scene.member):
        expect(person.locator(AGENT_TILE)).to_have_count(0)
    assert scene.agent.standings() == []


def test_a_member_outside_the_adders_group_is_offered_no_add(
    scene_of: Callable[[dict[str, Any]], Scene], open_person: Callable[..., Page], page_errors: list[str]
) -> None:
    """CALL-51: A seat outside the adders group shows no add, where a member's seat shows it."""
    scene = scene_of(DESKTOP)
    outsider = record_page_errors(open_person(**DESKTOP), page_errors)
    join_by_link(outsider, scene.link)

    scene.agent_waits()

    open_more(scene.member)
    expect(scene.member.locator(OFFER)).to_have_count(1)
    open_more(outsider)
    expect(outsider.locator(OFFER)).to_have_count(0)
