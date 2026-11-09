import secrets
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.support.calls import call_link_of, join_by_link, open_more, press_start_call, record_page_errors
from tests.e2e.support.dialed_agent import (
    ADDERS_GROUP,
    AGENT_NAME,
    OWNER_SUBJECT,
    OWNER_USERNAME,
    OwnAccountAgent,
    OwnAgent,
)
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture(scope="session")
def participation_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningServer]:
    """HopIn with participation wired into its calls, the adders group mapped to the owner `OWNER_SUBJECT`."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("participation-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp("participation-tls"),
        participation=True,
        agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
    ) as server:
        yield server


@pytest.fixture
def e2e_server(participation_server: RunningServer) -> RunningServer:
    return participation_server


@pytest.fixture
def own_account_agent(e2e_server: RunningServer) -> Iterator[OwnAccountAgent]:
    """The scripted agent of `OWNER_SUBJECT`, named apart from the agents earlier tests left registered."""
    agent = OwnAccountAgent(e2e_server, OWNER_SUBJECT, f"{AGENT_NAME}-{secrets.token_hex(3)}")
    yield agent
    agent.close()


@pytest.fixture
def grant_adders(e2e_server: RunningServer) -> Callable[[str], None]:
    """Make the issuer hold the adders group for the subject."""

    def grant(subject: str) -> None:
        e2e_server.issuer.grant_groups(subject, (ADDERS_GROUP,))

    return grant


@pytest.fixture
def own_agent_of(e2e_server: RunningServer) -> Iterator[Callable[[str], OwnAgent]]:
    """A person's own scripted agent by the person's account id; each ends with the test."""
    agents: list[OwnAgent] = []

    def make(account_id: str) -> OwnAgent:
        agents.append(OwnAgent(e2e_server, account_id))
        return agents[-1]

    yield make
    for agent in agents:
        agent.close()


OFFER = "#agentOffers button"
INVITATION_WAKE_SECONDS = 20


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

    def offers_on(self, page: Page) -> Locator:
        return page.locator(OFFER)

    def offer_of_agent(self, page: Page) -> Locator:
        return self.offers_on(page).filter(has_text=self.agent.name)

    def agent_listens(self) -> None:
        """The agent opens its stream, which registers it, so a member's seat is offered it; it holds no place yet."""
        self.agent.listen()
        assert self.agent.standings() == []

    def member_adds_and_the_agent_accepts(self) -> None:
        self.agent_listens()
        open_more(self.member)
        offer = self.offer_of_agent(self.member)
        expect(offer).to_have_text(f"Add {self.agent.name}")
        offer.click()
        expect(offer).to_have_text(f"Inviting {self.agent.name}…")
        expect(offer).to_be_disabled()
        accepted = self.agent.accept(self.agent.invitation_within(INVITATION_WAKE_SECONDS))
        assert accepted["name"] == self.agent.name


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
