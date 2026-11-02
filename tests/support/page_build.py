"""The committed page build the tests serve.

It holds a shell with one inline script, the three sign-in pages with their stylesheet,
the font it references, one immutable file and the version file.
"""

from pathlib import Path

from hopin.web.page_build import PageBuild

FIXTURE_DIRECTORY = Path(__file__).parent / "page_build_fixture"
# The SHA-256 of the fixture shell's one inline script, written out so no test derives it through production code.
FIXTURE_SCRIPT_HASH = "sha256-4LKaLFjkbG7q9xKX2UaD3tcSyvjsmzchYiSkLO5EkO8="
SIGN_IN_STYLESHEET = "/_app/immutable/assets/sign-in.css"
SIGN_IN_FONT = "/_app/immutable/assets/fixture-font.woff2"
IMMUTABLE_FILE = "/_app/immutable/x.js"
VERSION_FILE = "/_app/version.json"


def fixture_page_build() -> PageBuild:
    return PageBuild.load(FIXTURE_DIRECTORY)
