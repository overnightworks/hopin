"""Starts `hopin serve` over the committed fixture page build, because the shared-stores job has no Node and no build.

The first argument is the page build's directory.
"""

import sys
from pathlib import Path

import hopin.main

hopin.main.BUILD_DIRECTORY = Path(sys.argv[1])
hopin.main.main(["serve"])
