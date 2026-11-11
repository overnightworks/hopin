"""The committed page build the tests serve.

It holds a shell with one inline script, the three sign-in pages with their stylesheet,
the font it references, one immutable file, the immutable chunk that writes the route
announcer's template, and the version file.
"""

import base64
import hashlib
from pathlib import Path

from hopin.web.page_build import PageBuild

FIXTURE_DIRECTORY = Path(__file__).parent / "page_build_fixture"
# The SHA-256 of the fixture shell's one inline script, written out so no test derives it through production code.
FIXTURE_SCRIPT_HASH = "sha256-4LKaLFjkbG7q9xKX2UaD3tcSyvjsmzchYiSkLO5EkO8="
# The style attribute of the announcer template in the fixture's immutable chunk, as the build writes it.
FIXTURE_ANNOUNCER_STYLE = (
    "position: absolute; left: 0; top: 0; clip: rect(0 0 0 0); clip-path: inset(50%); overflow: hidden; "
    "white-space: nowrap; width: 1px; height: 1px"
)
SIGN_IN_STYLESHEET = "/_app/immutable/assets/sign-in.css"
SIGN_IN_FONT = "/_app/immutable/assets/fixture-font.woff2"
IMMUTABLE_FILE = "/_app/immutable/x.js"
VERSION_FILE = "/_app/version.json"


def fixture_page_build() -> PageBuild:
    return PageBuild.load(FIXTURE_DIRECTORY)


def announcer_style_sources(style: str) -> tuple[str, str]:
    """The `style-src` sources that admit one inline style: its keyword and its hash, computed here from the text."""
    digest = base64.b64encode(hashlib.sha256(style.encode("utf-8")).digest()).decode("ascii")
    return ("'unsafe-hashes'", f"'sha256-{digest}'")
