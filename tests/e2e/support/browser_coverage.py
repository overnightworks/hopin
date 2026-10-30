"""Chromium's precise V8 coverage of every page an end-to-end test opens, written for the CI report.

Collecting is off unless the environment names a directory, so local runs pay nothing.
"""

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, CDPSession, Page

BROWSER_COVERAGE_DIRECTORY_VARIABLE = "HOPIN_BROWSER_COVERAGE_DIR"


class BrowserCoverage:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._sessions: dict[Page, CDPSession] = {}

    @classmethod
    def from_environment(cls) -> BrowserCoverage | None:
        directory = os.environ.get(BROWSER_COVERAGE_DIRECTORY_VARIABLE)
        return None if not directory else cls(Path(directory))

    def watch(self, context: BrowserContext) -> None:
        """Start the profiler on every page the context opens, popups included."""
        context.on("page", self._start)

    def _start(self, page: Page) -> None:
        session = page.context.new_cdp_session(page)
        session.send("Profiler.enable")
        session.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
        self._sessions[page] = session

    def write(self) -> None:
        """Take the coverage of every page still open; call it before its context closes."""
        self._directory.mkdir(parents=True, exist_ok=True)
        for page, session in self._sessions.items():
            if not page.is_closed():
                taken = session.send("Profiler.takePreciseCoverage")
                (self._directory / f"{uuid4().hex}.json").write_text(json.dumps(taken))
