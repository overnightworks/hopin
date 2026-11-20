"""What an e2e test sees of the hearing sockets a page opens, and the waits that go with it."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from hopin.config.constants import HEALTH_PATH
from hopin.participation.vocabulary import HEARING_PATH

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page, WebSocket

HOP_SECONDS = 20
NO_HOP_WINDOW_MILLISECONDS = 1_000
POLL_MILLISECONDS = 50


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


def on_the_page_clock(open_person: Callable[..., Page]) -> Callable[..., Page]:
    """`open_person` whose pages run on the page clock from their first load, which `settle` moves."""

    def open_clocked(**options: Any) -> Page:
        page = open_person(**options)
        page.clock.install()
        return page

    return open_clocked


@pytest.fixture
def open_person(open_person: Callable[..., Page]) -> Callable[..., Page]:
    """Overrides the suite's `open_person` in a module that imports it: its pages run on the page clock."""
    return on_the_page_clock(open_person)


def settle(page: Page) -> None:
    """A window in which a hop that should not open would have: the page clock runs through it at once, and one real
    round trip to the server follows, so what the server sent in reply to the state change has been handled."""
    page.clock.run_for(NO_HOP_WINDOW_MILLISECONDS)
    page.evaluate("(path) => fetch(path).then((answer) => answer.status)", HEALTH_PATH)
    page.clock.run_for(NO_HOP_WINDOW_MILLISECONDS)
