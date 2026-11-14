"""The committed page build the tests serve.

It holds a shell with one inline script, the three sign-in pages with their stylesheet and icon,
the font the stylesheet references, one immutable file, the immutable chunk that writes the route
announcer's template, and the version file.
"""

from pathlib import Path

from hopin.web.page_build import PageBuild

FIXTURE_DIRECTORY = Path(__file__).parent / "page_build_fixture"
SIGN_IN_PAGE_FILES = ("login.html", "login-failed.html", "no-access.html")
# The SHA-256 of the fixture shell's one inline script, written out so no test derives it through production code.
FIXTURE_SCRIPT_HASH = "sha256-4LKaLFjkbG7q9xKX2UaD3tcSyvjsmzchYiSkLO5EkO8="
# The style attribute of the announcer template in the fixture's immutable chunk, as the build writes it.
FIXTURE_ANNOUNCER_STYLE = "position: absolute; width: 1px; height: 1px; overflow: hidden"
# The SHA-256 of `FIXTURE_ANNOUNCER_STYLE`, written out so no test derives it through production code.
FIXTURE_ANNOUNCER_HASH = "sha256-SazOE0PVlayjM4kGF6VE1PJhTVecf2rVGnIHlPrZIiU="
SIGN_IN_STYLESHEET = "/_app/immutable/assets/sign-in.css"
SIGN_IN_FONT = "/_app/immutable/assets/fixture-font.woff2"
SIGN_IN_ICON = "/_app/immutable/assets/fixture-icon.svg"
IMMUTABLE_FILE = "/_app/immutable/x.js"
VERSION_FILE = "/_app/version.json"


def fixture_page_build() -> PageBuild:
    return PageBuild.load(FIXTURE_DIRECTORY)
