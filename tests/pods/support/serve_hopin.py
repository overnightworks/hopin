"""Starts `hopin serve` over the committed fixture page build, because the shared-stores job has no Node and no build.

The first argument is the page build's directory; the server's wiring is production's.
"""

import sys
from pathlib import Path

from hopin.config.settings import ServerSettings
from hopin.main import serve
from hopin.web.page_build import PageBuild

serve(ServerSettings(), PageBuild.load(Path(sys.argv[1])))
