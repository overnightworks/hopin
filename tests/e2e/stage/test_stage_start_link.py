import re
from typing import TYPE_CHECKING

import pytest

from tests.e2e.stage.stage_page import TABLE_ID, expect_table_on_stage
from tests.e2e.support.calls import call_link_of, press_through_the_prejoin, record_page_errors, wait_until_in_the_call

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page

    from tests.e2e.stage.stub_skat import StubSkat
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

START_LINK_PATH = "/c/new"


def start_link(server: RunningServer, stub: StubSkat, app: str | None = None) -> str:
    return f"{server.base_url}{START_LINK_PATH}?app={app or stub.app.name}&context={TABLE_ID}"


def test_the_first_tap_starts_a_call_showing_the_table_and_a_later_tap_joins_it(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat, page_errors: list[str]
) -> None:
    """ST-14."""
    host = record_page_errors(open_person(), page_errors)
    host.goto(start_link(e2e_server, stub_skat))
    press_through_the_prejoin(host)
    wait_until_in_the_call(host)
    expect_table_on_stage(host, stub_skat)
    call_link = call_link_of(host)

    guest = record_page_errors(open_person(), page_errors)
    guest.goto(start_link(e2e_server, stub_skat))
    guest.wait_for_url(re.compile(re.escape(call_link)))
    press_through_the_prejoin(guest)
    wait_until_in_the_call(guest)

    expect_table_on_stage(guest, stub_skat)


def test_a_link_naming_no_registered_table_is_a_404(
    open_person: Callable[..., Page], e2e_server: RunningServer, stub_skat: StubSkat
) -> None:
    """ST-14."""
    person = open_person()

    response = person.goto(start_link(e2e_server, stub_skat, app="chess"))

    assert response is not None
    assert response.status == 404
