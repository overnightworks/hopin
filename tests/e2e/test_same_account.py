from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.support.calls import join_by_link, open_more, record_page_errors, start_call, toggle_the_lock
from tests.e2e.support.media import MEDIA_FLOWING_TIMEOUT_MILLISECONDS, OTHER_TILE
from tests.e2e.support.people import DESKTOP, PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.e2e

OTHERS_IN_THE_CALL = 2


def test_account_in_the_call_on_two_devices_stays_host_while_either_is_there(
    open_person: Callable[..., Page], base_url: str, page_errors: list[str]
) -> None:
    laptop = record_page_errors(open_person(**DESKTOP), page_errors)
    invite_link = start_call(laptop, base_url)
    phone = record_page_errors(open_person(same_account_as=laptop, **PHONE), page_errors)
    join_by_link(phone, invite_link)
    guest = record_page_errors(open_person(**DESKTOP), page_errors)
    join_by_link(guest, invite_link)
    for person in (laptop, phone, guest):
        expect(person.locator(OTHER_TILE)).to_have_count(OTHERS_IN_THE_CALL, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    expect(open_more(phone).locator("#lockBtn")).to_be_visible()

    laptop.click("#leaveBtn")
    laptop.click("#leaveYes")
    for remaining in (phone, guest):
        expect(remaining.locator(OTHER_TILE)).to_have_count(1, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    toggle_the_lock(phone)

    expect(phone.locator("#lockIndicator")).to_be_visible()
