from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from tests.e2e.support.browser_coverage import BrowserCoverage

if TYPE_CHECKING:
    from pathlib import Path


class FakePage:
    def __init__(self) -> None:
        self.main_frame = SimpleNamespace(page=self)
        self.navigations: list[str] = []

    def evaluate(self, _script: str, url: str) -> None:
        self.navigations.append(url)


class FakeRequest:
    method = "GET"

    def __init__(self, page: FakePage, url: str) -> None:
        self.frame = page.main_frame
        self.url = url


class FakeRoute:
    def __init__(self, page: FakePage, url: str) -> None:
        self.request = FakeRequest(page, url)
        self.fulfilled: dict[str, Any] | None = None

    def fulfill(self, **answer: Any) -> None:
        self.fulfilled = answer


def collector_watching(page: FakePage, tmp_path: Path) -> BrowserCoverage:
    collector = BrowserCoverage(tmp_path)
    collector._sessions[page] = SimpleNamespace(send=lambda *_: {})  # noqa: SLF001
    collector._with_app_code.add(page)  # noqa: SLF001
    return collector


def test_cancel_take_reissue_keeps_the_app_code_mark_through_the_cancelled_pass(tmp_path: Path) -> None:
    page = FakePage()
    collector = collector_watching(page, tmp_path)
    url = "http://host/room"

    assert collector.cancel_take_reissue(FakeRoute(page, url))  # type: ignore[arg-type]
    assert page in collector._with_app_code  # noqa: SLF001
    assert not collector.cancel_take_reissue(FakeRoute(page, url))  # type: ignore[arg-type]
    assert page not in collector._with_app_code  # noqa: SLF001
