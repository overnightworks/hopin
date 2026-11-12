import secrets
from typing import TYPE_CHECKING, Any

import pytest
from agents.member import LISTEN_ALLOWANCE_SECONDS
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import join_by_link, open_more, record_page_errors
from tests.e2e.support.dialed_agent import (
    AGENT_NAME,
    AGENT_TILE,
    OWNER_SUBJECT,
    OWNER_USERNAME,
    REMOVE,
    OwnAccountAgent,
)
from tests.e2e.support.people import DESKTOP, FORM_FACTORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.e2e.support.participation_scene import Scene
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

LEAVE = "#leaveBtn"
QUIET_SECONDS = 2
LAPSE_WITHIN_MILLISECONDS = (LISTEN_ALLOWANCE_SECONDS + 10) * 1000


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


def test_an_agent_with_a_lease_and_no_listen_is_offered_to_nobody(scene_of: Callable[[dict[str, Any]], Scene]) -> None:
    """CALL-51: An agent that renews its lease without a listen on its standing is offered to no seat."""
    scene = scene_of(DESKTOP)
    assert scene.agent.standings() == []

    open_more(scene.member)
    scene.member.wait_for_timeout(QUIET_SECONDS * 1000)

    expect(scene.offers_on(scene.member)).to_have_count(0)


def test_a_listen_that_ends_without_a_next_one_drops_the_offer_of_the_seat_that_showed_it(
    scene_of: Callable[[dict[str, Any]], Scene],
) -> None:
    """CALL-51: An agent's offer ends once its last listen on the standing has ended and its allowance has passed."""
    scene = scene_of(DESKTOP)
    scene.agent_listens()
    open_more(scene.member)
    expect(scene.offer_of_agent(scene.member)).to_have_count(1)

    scene.agent.stop_listening()

    expect(scene.offer_of_agent(scene.member)).to_have_count(0, timeout=LAPSE_WITHIN_MILLISECONDS)


def test_another_agents_stream_is_not_woken_by_this_agents_invitation(
    scene_of: Callable[[dict[str, Any]], Scene], e2e_server: RunningServer
) -> None:
    """The stream of an agent of another owner stays asleep while this agent's invitation wakes this agent."""
    scene = scene_of(DESKTOP)
    bystander = OwnAccountAgent(e2e_server, "subject-another-owner", f"{AGENT_NAME}-{secrets.token_hex(3)}")
    try:
        bystander.listen()

        scene.member_adds_and_the_agent_accepts()

        assert not bystander.woken_within(QUIET_SECONDS)
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
    expect(scene.offers_on(scene.member)).to_have_count(0)
    expect(scene.member.locator("#agentOffers")).to_be_empty()


def test_a_refusal_leaves_when_the_offers_change_after_it(
    scene_of: Callable[[dict[str, Any]], Scene], e2e_server: RunningServer
) -> None:
    """CALL-51: A refused add stays until the person calls again, closes "Mehr" or an offers frame follows it."""
    scene = scene_of(DESKTOP)
    scene.agent_listens()
    open_more(scene.member)
    expect(scene.offer_of_agent(scene.member)).to_have_count(1)
    e2e_server.issuer.refuses_refresh_grants = True
    try:
        scene.offer_of_agent(scene.member).get_by_role("button").click()
        expect(scene.member.get_by_role("alert")).to_be_visible()
    finally:
        e2e_server.issuer.refuses_refresh_grants = False
    second = OwnAccountAgent(e2e_server, OWNER_SUBJECT, f"{AGENT_NAME}-{secrets.token_hex(3)}")
    try:
        second.listen()

        expect(scene.offers_on(scene.member).filter(has_text=second.name)).to_have_count(1)
        expect(scene.member.get_by_role("alert")).to_have_count(0)
    finally:
        second.close()
