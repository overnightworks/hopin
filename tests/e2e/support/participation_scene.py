from typing import TYPE_CHECKING, Any

from playwright.sync_api import expect

from tests.e2e.support.calls import call_link_of, join_by_link, open_more, press_start_call, record_page_errors
from tests.e2e.support.dialed_agent import OWNER_USERNAME

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Locator, Page

    from tests.e2e.support.dialed_agent import OwnAccountAgent
    from tests.e2e.support.server import RunningServer

OFFER = "#agentOffers li"
INVITATION_WAKE_SECONDS = 20


class Scene:
    """The host, the agent's owner and a member of the adders group in one call, and the owner's scripted agent. The
    agent registers with its first call (`agent_listens` or `standings`); it holds no place yet. The member adds the
    agents, or the owner from their own seat when `owner_adds`."""

    def __init__(
        self,
        open_person: Callable[..., Page],
        member_of: Callable[[Page], Any],
        server: RunningServer,
        page_errors: list[str],
        form_factor: dict[str, Any],
        grant_adders: Callable[[str], None],
        agent: OwnAccountAgent,
        *,
        owner_adds: bool = False,
    ):
        self.agent = agent
        self.host = record_page_errors(open_person(**form_factor), page_errors)
        press_start_call(self.host, server.base_url)
        self.link = call_link_of(self.host)
        self.owner = record_page_errors(open_person(username=OWNER_USERNAME, **form_factor), page_errors)
        if owner_adds:
            grant_adders(member_of(self.owner).account.id)
        join_by_link(self.owner, self.link)
        self.member = record_page_errors(open_person(**form_factor), page_errors)
        grant_adders(member_of(self.member).account.id)
        join_by_link(self.member, self.link)
        self.adder = self.owner if owner_adds else self.member
        self.everyone = (self.host, self.owner, self.member)

    def offers_on(self, page: Page) -> Locator:
        return page.locator(OFFER)

    def offer_of_agent(self, page: Page) -> Locator:
        return self.offer_of(page, self.agent)

    def offer_of(self, page: Page, agent: OwnAccountAgent) -> Locator:
        return self.offers_on(page).filter(has_text=agent.name)

    def agent_listens(self) -> None:
        """The agent opens its stream, which registers it, so a member's seat is offered it; it holds no place yet."""
        self.agent.listen()
        assert self.agent.standings() == []

    def member_adds_and_the_agent_accepts(self, figure: dict[str, str] | None = None) -> None:
        self.member_adds_and_accepts(self.agent, figure)

    def member_adds_and_accepts(self, agent: OwnAccountAgent, figure: dict[str, str] | None = None) -> None:
        """The member adds the agent from its offer and the agent accepts the invitation."""
        self.member_adds(agent)
        accepted = agent.accept(agent.invitation_within(INVITATION_WAKE_SECONDS), figure)
        assert accepted["name"] == agent.name

    def member_adds(self, agent: OwnAccountAgent) -> None:
        """The agent opens its stream and the adder adds it from its offer, which then shows the invitation."""
        agent.listen()
        assert agent.standings() == []
        open_more(self.adder)
        offer = self.offer_of(self.adder, agent)
        expect(offer).to_have_count(1)
        offer.get_by_role("button").click()
        expect(offer.get_by_role("status")).to_be_visible()
        expect(offer.get_by_role("button")).to_have_count(0)
