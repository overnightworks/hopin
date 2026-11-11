import secrets
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import join_by_link, open_more, record_page_errors
from tests.e2e.support.dialed_agent import AGENT_NAME, AGENT_TILE, OWNER_USERNAME, REMOVE, OwnAccountAgent
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.participation_scene import Scene
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

LEAVE = "#leaveBtn"
BYSTANDER_QUIET_SECONDS = 2


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
        expect(tile.locator(".name")).to_contain_text(scene.agent.name)
        expect(tile.locator(".ai-badge")).to_be_visible()
        expect(tile.locator(".sponsor")).to_have_text(OWNER_USERNAME)
    expect(scene.member.locator(AGENT_TILE + " " + REMOVE)).to_have_count(0)
    assert [standing["state"] for standing in scene.agent.standings()] == ["active"]

    scene.host.locator(AGENT_TILE + " " + REMOVE).click()

    for person in scene.everyone:
        expect(person.locator(AGENT_TILE)).to_have_count(0)
    assert scene.agent.standings() == []


def test_another_agents_stream_is_not_woken_by_this_agents_invitation(
    scene_of: Callable[[dict[str, Any]], Scene], e2e_server: RunningServer
) -> None:
    """The stream of an agent of another owner stays asleep while this agent's invitation wakes this agent."""
    scene = scene_of(DESKTOP)
    bystander = OwnAccountAgent(e2e_server, "subject-another-owner", f"{AGENT_NAME}-{secrets.token_hex(3)}")
    try:
        bystander.listen()

        scene.member_adds_and_the_agent_accepts()

        assert not bystander.woken_within(BYSTANDER_QUIET_SECONDS)
    finally:
        bystander.close()


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

    scene.agent_listens()

    open_more(scene.member)
    expect(scene.offer_of_agent(scene.member)).to_have_count(1)
    open_more(outsider)
    expect(scene.offers_on(outsider)).to_have_count(0)


def test_a_refusal_after_the_adders_right_was_revoked_leaves_when_more_is_closed(
    scene_of: Callable[[dict[str, Any]], Scene],
    e2e_server: RunningServer,
    member_of: Callable[[Page], Any],
) -> None:
    """CALL-51: A refused add shows as one of the control's reasons and leaves when the person closes More."""
    scene = scene_of(DESKTOP)
    scene.agent_listens()
    open_more(scene.member)
    e2e_server.issuer.grant_groups(member_of(scene.member).account.id, ())

    scene.offer_of_agent(scene.member).get_by_role("button").click()

    expect(scene.member.get_by_role("alert")).to_be_visible()

    scene.member.keyboard.press("Escape")
    open_more(scene.member)
    expect(scene.member.get_by_role("alert")).to_have_count(0)
