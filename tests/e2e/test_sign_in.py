from collections.abc import Callable
from typing import Any

import pytest
from playwright.sync_api import Page
from starlette.requests import Request
from webauth.dependencies import admit_session, session_id_from_request
from webauth.ports import AuthenticatedUser

from tests.e2e.conftest import DESKTOP, PHONE, RunningServer
from tests.signed_in import SeededMember

pytestmark = pytest.mark.e2e

FORM_FACTORS = pytest.mark.parametrize("form_factor", [DESKTOP, PHONE], ids=["desktop", "phone"])


def open_home(page: Page, base_url: str) -> str:
    """Open the home page and answer the Cookie header its document request carried."""
    response = page.goto(f"{base_url}/")
    assert response is not None
    return response.request.all_headers()["cookie"]


def admitted_account(server: RunningServer, cookie_header: str) -> AuthenticatedUser:
    request = Request({"type": "http", "headers": [(b"cookie", cookie_header.encode())]})
    session_id = session_id_from_request(request, server.config)
    assert session_id is not None
    admitted = admit_session(session_id, sessions=server.stores.sessions, config=server.config)
    assert isinstance(admitted, AuthenticatedUser)
    return admitted


@FORM_FACTORS
def test_open_person_holds_the_session_and_csrf_cookies_of_its_seeded_member(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], SeededMember],
    e2e_server: RunningServer,
    form_factor: dict[str, Any],
) -> None:
    page = open_person(**form_factor)
    cookie_header = open_home(page, e2e_server.base_url)

    held = {cookie["name"]: cookie for cookie in page.context.cookies()}
    config = e2e_server.config
    assert set(held) == {config.session_cookie_name, config.csrf_cookie_name}
    for cookie in held.values():
        assert (cookie["secure"], cookie["path"], cookie["sameSite"]) == (True, "/", "Lax")
        assert cookie["expires"] > config.wall_clock()
    assert held[config.session_cookie_name]["httpOnly"] is True
    assert held[config.csrf_cookie_name]["httpOnly"] is False
    assert all(f"{name}={cookie['value']}" in cookie_header for name, cookie in held.items())
    assert admitted_account(e2e_server, cookie_header).id == member_of(page).account.id


def test_two_people_are_two_members_each_admitted_as_itself(
    open_person: Callable[..., Page], member_of: Callable[[Page], SeededMember], e2e_server: RunningServer
) -> None:
    first, second = open_person(**DESKTOP), open_person(**DESKTOP)
    first_cookie_header = open_home(first, e2e_server.base_url)
    second_cookie_header = open_home(second, e2e_server.base_url)

    assert member_of(first).account.id != member_of(second).account.id
    assert admitted_account(e2e_server, first_cookie_header).id == member_of(first).account.id
    assert admitted_account(e2e_server, second_cookie_header).id == member_of(second).account.id


def test_a_person_opened_signed_out_holds_no_cookie(
    open_person: Callable[..., Page], e2e_server: RunningServer
) -> None:
    page = open_person(signed_in=False, **DESKTOP)
    page.goto(f"{e2e_server.base_url}/")

    assert page.context.cookies() == []
