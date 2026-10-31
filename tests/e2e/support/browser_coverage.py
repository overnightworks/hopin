"""Chromium's precise V8 coverage of every page an end-to-end test opens, written for the CI report.

Collecting is off unless the environment names a directory, so local runs pay nothing.
A page-started document navigation would drop its page's counters, so the collector cancels it
with a 204, takes the page's coverage and re-issues the URL; the re-issue goes to the server.
"""

import json
import os
import re
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from playwright.sync_api import BrowserContext, CDPSession, Frame, Page, Request, Route

BROWSER_COVERAGE_DIRECTORY_VARIABLE = "HOPIN_BROWSER_COVERAGE_DIR"
APP_SCRIPT_PATH_PREFIX = "/_app/"
NO_CONTENT = 204
# The driver evaluates this as a JavaScript regex against the whole URL, so requests under
# /_app/ never reach Python. The literal "/" after the host keeps backtracking from dodging the lookahead.
ROUTED_URL_PATTERN = re.compile(rf"^[^:]+://[^/]+/(?!{re.escape(APP_SCRIPT_PATH_PREFIX.lstrip('/'))})")


def frame_of(request: Request) -> Frame | None:
    """None for the first request of a new tab, which Chromium issues before the tab's frame exists."""
    try:
        return request.frame
    except PlaywrightError:
        return None


class BrowserCoverage:
    def __init__(self, directory: Path | None) -> None:
        self._directory = directory
        self._sessions: dict[Page, CDPSession] = {}
        self._with_app_code: set[Page] = set()
        self._reissue_pending: set[tuple[Page, str]] = set()
        self._cancelled: set[Request] = set()
        self._reissue_counts: Counter[Page] = Counter()
        self._passing_through: set[Page] = set()

    @classmethod
    def from_environment(cls) -> BrowserCoverage:
        directory = os.environ.get(BROWSER_COVERAGE_DIRECTORY_VARIABLE)
        return cls(Path(directory) if directory else None)

    def watch(self, context: BrowserContext) -> None:
        """Start the profiler on every page the context opens, popups included."""
        if self._directory is None:
            return
        context.on("page", self._start)
        context.on("request", self._mark_app_code)
        context.route(ROUTED_URL_PATTERN, self._on_request)

    def was_cancelled(self, request: Request) -> bool:
        """Whether the collector answered this request with 204 and re-issued it."""
        return request in self._cancelled

    def reissued(self, page: Page) -> int:
        """How many navigations of this page the collector re-issued."""
        return self._reissue_counts[page]

    @contextmanager
    def passing_through(self, page: Page) -> Iterator[None]:
        """Let the page's navigations go through untouched, for one a test drives itself."""
        self._passing_through.add(page)
        try:
            yield
        finally:
            self._passing_through.discard(page)

    def cancel_take_reissue(self, route: Route) -> bool:
        """True when this pass was cancelled, taken and re-issued; False when it goes through.

        A test whose own page route answers a navigation calls this first, because page routes run
        before the context route.
        """
        request = route.request
        frame = frame_of(request)
        if frame is None:
            return False
        page = frame.page
        reissue = (page, request.url)
        if reissue in self._reissue_pending:
            self._reissue_pending.discard(reissue)
            self._with_app_code.discard(page)
            return False
        if (
            page not in self._with_app_code
            or page in self._passing_through
            or request.method != "GET"
            or page not in self._sessions
        ):
            self._with_app_code.discard(page)
            return False
        route.fulfill(status=NO_CONTENT)
        self._cancelled.add(request)
        self._take(page)
        self._reissue_pending.add(reissue)
        self._reissue_counts[page] += 1
        page.evaluate("url => location.assign(url)", request.url)
        return True

    def _mark_app_code(self, request: Request) -> None:
        frame = frame_of(request)
        if (
            frame is not None
            and frame.page.main_frame is frame
            and request.resource_type == "script"
            and urlsplit(request.url).path.startswith(APP_SCRIPT_PATH_PREFIX)
        ):
            self._with_app_code.add(frame.page)

    def _on_request(self, route: Route) -> None:
        request = route.request
        frame = frame_of(request)
        if (
            frame is not None
            and frame.page.main_frame is frame
            and request.resource_type == "document"
            and self.cancel_take_reissue(route)
        ):
            return
        route.fallback()

    def _start(self, page: Page) -> None:
        session = page.context.new_cdp_session(page)
        session.send("Profiler.enable")
        session.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
        self._sessions[page] = session

    def _take(self, page: Page) -> None:
        assert self._directory is not None
        self._directory.mkdir(parents=True, exist_ok=True)
        taken = self._sessions[page].send("Profiler.takePreciseCoverage")
        (self._directory / f"{uuid4().hex}.json").write_text(json.dumps(taken))

    def write(self) -> None:
        """Take the coverage of every page still open; call it before its context closes."""
        for page in self._sessions:
            if not page.is_closed():
                self._take(page)
