from typing import TYPE_CHECKING, Any

import pytest
import uvicorn
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.game import GameSettings, game_feature
from hopin.main import uvicorn_options
from tests.e2e.support.media import FAKE_MEDIA_BROWSER_ARGS, MEDIA_PERMISSIONS
from tests.e2e.support.people import PERSON_ADDRESS_BASE, _playwright_cookie, person_address_offsets
from tests.e2e.support.server import (
    LOOPBACK_HOST,
    RunningServer,
    _reserve_free_port,
    _serving,
    _write_self_signed_certificate,
)
from tests.game.support.fake_agent_service import FakeAgentService
from tests.support.fake_issuer import FakeIssuer
from tests.support.sign_in_settings import build_sign_in_settings
from tests.support.signed_in import SeededMember, new_account, seed_member

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from playwright.sync_api import Browser, BrowserContext, Page


@pytest.fixture(scope="session")
def e2e_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningServer]:
    port = _reserve_free_port()
    sign_in = build_sign_in_settings(
        tmp_path_factory.mktemp("sign-in"),
        host=LOOPBACK_HOST,
        port=port,
        public_origin=f"https://{LOOPBACK_HOST}:{port}",
        trusted_proxies=LOOPBACK_HOST,
    )
    stores = SignInStores.in_memory()
    issuer = FakeIssuer(sign_in)
    agent_service = FakeAgentService(issuer)
    app = create_app(
        sign_in.settings,
        stores=stores,
        issuer_transports=issuer.transports,
        call_feature=game_feature(GameSettings(), transport=agent_service.transport),
    )
    key_file, certificate_file = _write_self_signed_certificate(tmp_path_factory.mktemp("tls"))
    server = uvicorn.Server(
        uvicorn.Config(
            app, **uvicorn_options(sign_in.settings), ssl_keyfile=str(key_file), ssl_certfile=str(certificate_file)
        )
    )
    with _serving(server):
        yield RunningServer(
            f"https://{LOOPBACK_HOST}:{port}", installed_web_auth_config(app), stores, issuer, agent_service
        )


@pytest.fixture(scope="session")
def base_url(e2e_server: RunningServer) -> str:
    return e2e_server.base_url


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_type_launch_args, "args": FAKE_MEDIA_BROWSER_ARGS}


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
def open_person(
    browser: Browser,
    browser_context_args: dict[str, Any],
    e2e_server: RunningServer,
    seeded_members: dict[Page, SeededMember],
) -> Iterator[Callable[..., Page]]:
    contexts: list[BrowserContext] = []

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


@pytest.fixture
def page_errors() -> Iterator[list[str]]:
    errors: list[str] = []
    yield errors
    assert errors == []
