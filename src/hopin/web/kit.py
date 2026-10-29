from importlib.metadata import version
from importlib.resources import files
from pathlib import Path

from hopin.config.constants import STATIC_PATH

KIT_PACKAGE = "overnightworks-appkit"
KIT_DIR = Path(str(files("appkit") / "static"))
KIT_PATH = f"{STATIC_PATH}/appkit-{version(KIT_PACKAGE)}"
PUBLIC_KIT_SUFFIXES = frozenset({".css", ".woff2"})
# Exact files only: the kit's licence files stay behind the session gate because no signed-out page fetches them.
PUBLIC_KIT_PATHS = frozenset(
    f"{KIT_PATH}/{file.relative_to(KIT_DIR).as_posix()}"
    for file in KIT_DIR.rglob("*")
    if file.suffix in PUBLIC_KIT_SUFFIXES
)
