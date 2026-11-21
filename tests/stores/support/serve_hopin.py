"""Starts `hopin serve` over the committed fixture page build, because the shared-stores job has no Node and no build.

The first argument is the page build's directory; the server's wiring is production's. An optional second argument
is the participation feature's wait for its first subscription, in seconds, so a test of a start that never gets one
ends in a moment instead of production's wait.
"""

import sys
from pathlib import Path

from hopin.agent.mcp import PLACE_SUBSCRIPTION_WAIT_SECONDS
from hopin.config.settings import RealmSettings, ServerSettings
from hopin.main import serve_on_migrated_database
from hopin.web.page_build import PageBuild

page_build_directory, *subscription_wait = sys.argv[1:]
serve_on_migrated_database(
    ServerSettings(),
    RealmSettings(),
    PageBuild.load(Path(page_build_directory)),
    subscription_wait_seconds=float(subscription_wait[0]) if subscription_wait else PLACE_SUBSCRIPTION_WAIT_SECONDS,
)
