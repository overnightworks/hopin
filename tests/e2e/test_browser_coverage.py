"""The browser coverage collector's teardown does not depend on when a page opens."""

from typing import TYPE_CHECKING, Any, cast

from tests.e2e.support.browser_coverage import BrowserCoverage

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from playwright.sync_api import BrowserContext


class FakeSession:
    def __init__(self, context: FakeContext) -> None:
        self._context = context

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        del params
        if method == "Profiler.takePreciseCoverage":
            self._context.on_take()
        return {"result": []}


class FakeContext:
    """A context whose pages the test opens through the handler the collector registered."""

    def __init__(self) -> None:
        self.page_handlers: list[Callable[[Any], None]] = []
        self.on_take: Callable[[], None] = lambda: None

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        if event == "page":
            self.page_handlers.append(handler)

    def route(self, pattern: object, handler: object) -> None:
        del pattern, handler

    def new_cdp_session(self, page: object) -> FakeSession:
        del page
        return FakeSession(self)


class FakePage:
    def __init__(self, context: FakeContext) -> None:
        self.context = context

    def is_closed(self) -> bool:
        return False


def open_page(context: FakeContext) -> FakePage:
    page = FakePage(context)
    for handler in context.page_handlers:
        handler(page)
    return page


def test_a_page_opened_while_the_coverage_is_written_is_written_too(tmp_path: Path) -> None:
    collector = BrowserCoverage(tmp_path)
    context = FakeContext()
    collector.watch(cast("BrowserContext", context))
    open_page(context)
    opened: list[FakePage] = []

    def open_the_late_page() -> None:
        context.on_take = lambda: None
        opened.append(open_page(context))

    context.on_take = open_the_late_page

    collector.write()

    assert len(opened) == 1
    assert len(list(tmp_path.glob("*.json"))) == 2
