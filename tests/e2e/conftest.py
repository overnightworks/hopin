import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import pytest
import structlog.testing
from webauth.admission import AppRole
from webauth.ports import AccountKind

from tests.e2e.support.browser_coverage import BrowserCoverage
from tests.e2e.support.camera_less_call import (
    CameraLessCall,
    CameraLessHostCall,
    start_camera_less_call,
    start_camera_less_host_call,
)
from tests.e2e.support.dialed_agent import Agent
from tests.e2e.support.media import FAKE_MEDIA_BROWSER_ARGS, MEDIA_PERMISSIONS, NO_SOUND_DEVICE_ENVIRONMENT
from tests.e2e.support.mesh_pair import ConnectedPair, pair_connector
from tests.e2e.support.people import DESKTOP, PERSON_ADDRESS_BASE, person_address_offsets, playwright_cookie
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.e2e.support.shards import select_shard
from tests.support.signed_in import SeededMember, new_account, seed_member

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from playwright.sync_api import Browser, BrowserContext, Page, Request

PAGE_NAVIGATED_AWAY = "net::ERR_ABORTED"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    select_shard(config, items, Path(__file__).parent)


@pytest.fixture(scope="session")
def core_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningServer]:
    """HopIn with no feature wired into its calls."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("sign-in"))
    with running_hopin(loopback, tmp_path_factory.mktemp("tls")) as server:
        yield server


@pytest.fixture
def e2e_server(core_server: RunningServer) -> RunningServer:
    """The server a test's people use; a directory whose tests need a feature overrides it."""
    return core_server


@pytest.fixture(scope="session")
def base_url(core_server: RunningServer) -> str:
    return core_server.base_url


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    # The test certificate is self-signed: the per-request waiver of ignore_https_errors restarts a request
    # once per failed handshake, up to Chromium's cap, and a stylesheet that hits it never loads.
    return {
        **browser_type_launch_args,
        "args": [*FAKE_MEDIA_BROWSER_ARGS, "--ignore-certificate-errors"],
        "env": {**os.environ, **NO_SOUND_DEVICE_ENVIRONMENT},
    }


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    # The suite reads the German words; without a locale Chromium asks for English, which HopIn offers too.
    return {**browser_context_args, "permissions": MEDIA_PERMISSIONS, "ignore_https_errors": True, "locale": "de-DE"}


@pytest.fixture
def seeded_members() -> dict[Page, SeededMember]:
    return {}


@pytest.fixture
def member_of(seeded_members: dict[Page, SeededMember]) -> Callable[[Page], SeededMember]:
    return seeded_members.__getitem__


@pytest.fixture
def agent_of(e2e_server: RunningServer, member_of: Callable[..., Any]) -> Callable[[Page], Agent]:
    agents: list[Agent] = []

    def make(person: Page) -> Agent:
        agents.append(Agent(e2e_server, person, member_of))
        return agents[-1]

    yield make
    for agent in agents:
        agent.close()


@pytest.fixture
def expected_request_failures() -> list[str]:
    """URL prefixes of requests a test breaks on purpose; it appends them before the request is made."""
    return []


@pytest.fixture
def browser_coverage() -> BrowserCoverage:
    """The one collector of this test; with collection off it routes nothing and records nothing."""
    return BrowserCoverage.from_environment()


@pytest.fixture
def open_person(
    browser: Browser,
    browser_context_args: dict[str, Any],
    e2e_server: RunningServer,
    seeded_members: dict[Page, SeededMember],
    expected_request_failures: list[str],
    browser_coverage: BrowserCoverage,
) -> Iterator[Callable[..., Page]]:
    contexts: list[BrowserContext] = []
    failed_requests: list[str] = []

    def note_failed_request(request: Request) -> None:
        if request.failure != PAGE_NAVIGATED_AWAY and not request.url.startswith(tuple(expected_request_failures)):
            failed_requests.append(f"{request.url}: {request.failure}")

    def open_page(
        *,
        signed_in: bool = True,
        same_account_as: Page | None = None,
        username: str | None = None,
        with_refresh_token: bool = True,
        **device_options: Any,
    ) -> Page:
        person_address = PERSON_ADDRESS_BASE + next(person_address_offsets)
        context = browser.new_context(
            **{**browser_context_args, "extra_http_headers": {"X-Forwarded-For": str(person_address)}, **device_options}
        )
        contexts.append(context)
        browser_coverage.watch(context)
        context.on("requestfailed", note_failed_request)
        page = context.new_page()
        if signed_in:
            account = None if same_account_as is None else seeded_members[same_account_as].account
            if username is not None:
                account = e2e_server.stores.accounts.create_for_subject(
                    f"subject-{username}", username, role=AppRole.USER, kind=AccountKind.PERSON
                )
            if account is None:
                account = new_account(e2e_server.stores, e2e_server.config)
            refresh_token = e2e_server.issuer.refresh_token_for(account.id) if with_refresh_token else None
            member = seed_member(e2e_server.stores, e2e_server.config, account=account, refresh_token=refresh_token)
            context.add_cookies(
                [
                    playwright_cookie(header, e2e_server.config, urlsplit(e2e_server.base_url).hostname)
                    for header in member.set_cookie_headers
                ]
            )
            seeded_members[page] = member
        return page

    yield open_page
    try:
        browser_coverage.write()
    finally:
        # An open page keeps its socket to the server, which then waits out the closing handshake at its stop.
        for context in contexts:
            context.close()
    assert failed_requests == []


@pytest.fixture
def page_errors() -> Iterator[list[str]]:
    errors: list[str] = []
    yield errors
    assert errors == []


@pytest.fixture
def form_factor() -> dict[str, Any]:
    return DESKTOP


@pytest.fixture
def start_call_with(
    open_person: Callable[..., Page], base_url: str, form_factor: dict[str, Any], page_errors: list[str]
) -> Callable[..., Any]:
    """Runs a call arrangement with the person opener, the server address, the form factor and the error list."""

    def start(arrangement: Callable[..., Any], *extra: Any, **options: Any) -> Any:
        return arrangement(open_person, base_url, form_factor, page_errors, *extra, **options)

    return start


@pytest.fixture
def camera_less_call(start_call_with: Callable[..., Any]) -> CameraLessCall:
    return start_call_with(start_camera_less_call)


@pytest.fixture
def camera_less_host_call(start_call_with: Callable[..., Any]) -> CameraLessHostCall:
    return start_call_with(start_camera_less_host_call)


@pytest.fixture
def connect_pair(open_person: Callable[..., Page], base_url: str) -> Iterator[Callable[..., ConnectedPair]]:
    yield from pair_connector(open_person, base_url)


@pytest.fixture
def connected_pair(connect_pair: Callable[..., ConnectedPair]) -> ConnectedPair:
    return connect_pair()


_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class _EventCollector(logging.Handler):
    """Writes each standard-library record as the event it names and the fields it carries, like structlog's capture."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        super().__init__(logging.INFO)
        self._events = events

    def emit(self, record: logging.LogRecord) -> None:
        fields = {name: value for name, value in vars(record).items() if name not in _RECORD_ATTRIBUTES}
        self._events.append({"event": record.getMessage(), **fields})


@contextmanager
def _standard_library_events(events: list[dict[str, Any]]) -> Iterator[None]:
    logger = logging.getLogger("hopin")
    level = logger.level
    collector = _EventCollector(events)
    logger.setLevel(logging.INFO)
    logger.addHandler(collector)
    try:
        yield
    finally:
        logger.removeHandler(collector)
        logger.setLevel(level)


@pytest.fixture
def server_logs() -> Iterator[list[dict[str, Any]]]:
    """The server's log events from before the call is set up until the test ends, the structlog ones and the step
    lines `logline.StepTimer` writes through the standard library alike."""
    with structlog.testing.capture_logs() as logs, _standard_library_events(logs):
        yield logs
