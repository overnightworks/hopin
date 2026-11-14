"""Starts `hopin serve` over the committed fixture page build, because the shared-stores job has no Node and no build.

The first argument is the page build's directory.
"""

import sys
from pathlib import Path

import uvicorn

from hopin.config.settings import ServerSettings
from hopin.main import build_application, uvicorn_options
from hopin.stores.redis import connect_shared_stores
from hopin.web.page_build import PageBuild

settings = ServerSettings()
application = build_application(
    settings, PageBuild.load(Path(sys.argv[1])), connect_shared_stores(str(settings.redis_url))
)
uvicorn.run(application, **uvicorn_options(settings))
