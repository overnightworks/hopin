from typing import TYPE_CHECKING

from playwright.sync_api import Locator, Page, expect

from tests.e2e.support.calls import open_more
from tests.e2e.support.media import MEDIA_FLOWING_TIMEOUT_MILLISECONDS

if TYPE_CHECKING:
    from tests.e2e.skat.stub_skat import StubSkat

FRAME = ".skat-frame"
STRIP_TILES = "#videoGrid .tile"
REMOVE_TABLE = "button.skat-remove"
SHOW_ITEM = "#skatShowBtn"
LINK_FIELD = "#skatLink"
TOASTS = "#toasts"
TABLE_ID = "0123456789abcdef0123456789abcdef"


def table_link(stub: StubSkat, table_id: str = TABLE_ID) -> str:
    return f"{stub.origin}/t/{table_id}"


def show_table(person: Page, link: str) -> None:
    open_more(person).locator(SHOW_ITEM).click()
    person.fill(LINK_FIELD, link)
    person.press(LINK_FIELD, "Enter")


def expect_table_on_stage(person: Page, stub: StubSkat, table_id: str = TABLE_ID) -> None:
    expect(person.locator(FRAME)).to_have_attribute(
        "src", f"{table_link(stub, table_id)}?mode=call", timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )


def expect_no_table(person: Page) -> None:
    expect(person.locator(FRAME)).to_have_count(0)


def report_of(person: Page) -> Locator:
    return person.frame_locator(FRAME).locator("#report")
