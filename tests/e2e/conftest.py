from typing import TYPE_CHECKING, Any

import pytest

from tests.e2e.support.media import FAKE_MEDIA_BROWSER_ARGS, MEDIA_PERMISSIONS
from tests.e2e.support.people import PERSON_ADDRESS_BASE, _playwright_cookie, person_address_offsets
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.support.signed_in import SeededMember, new_account, seed_member

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from playwright.sync_api import Browser, BrowserContext, Page, Request

PAGE_NAVIGATED_AWAY = "net::ERR_ABORTED"


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
    return {**browser_type_launch_args, "args": [*FAKE_MEDIA_BROWSER_ARGS, "--ignore-certificate-errors"]}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_context_args, "permissions": MEDIA_PERMISSIONS, "ignore_https_errors": True}


@pytest.fixture
def seeded_members() -> dict[Page, SeededMember]:
    return {}


@pytest.fixture
def member_of(seeded_members: dict[Page, SeededMember]) -> Callable[[Page], SeededMember]:
    return seeded_members.__getitem__


@pytest.fixture
def expected_request_failures() -> list[str]:
    """URL prefixes of requests a test breaks on purpose; it appends them before the request is made."""
    return []


@pytest.fixture
def open_person(
    browser: Browser,
    browser_context_args: dict[str, Any],
    e2e_server: RunningServer,
    seeded_members: dict[Page, SeededMember],
    expected_request_failures: list[str],
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
        context.on("requestfailed", note_failed_request)
        page = context.new_page()
        if signed_in:
            account = None if same_account_as is None else seeded_members[same_account_as].account
            if username is not None:
                account = e2e_server.stores.accounts.create_for_subject(
                    f"subject-{username}", username, role=e2e_server.config.user_role
                )
            if account is None:
                account = new_account(e2e_server.stores, e2e_server.config)
            refresh_token = e2e_server.issuer.refresh_token_for(account.id) if with_refresh_token else None
            member = seed_member(e2e_server.stores, e2e_server.config, account=account, refresh_token=refresh_token)
            context.add_cookies([_playwright_cookie(header, e2e_server.config) for header in member.set_cookie_headers])
            seeded_members[page] = member
        return page

    yield open_page
    for context in contexts:
        context.close()
    assert failed_requests == []


@pytest.fixture
def page_errors() -> Iterator[list[str]]:
    errors: list[str] = []
    yield errors
    assert errors == []
