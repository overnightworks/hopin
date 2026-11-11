import json
from enum import StrEnum
from pathlib import Path

from hopin.game.vocabulary import FEATURE_NAME

CLIENT_MANIFEST = (
    Path(__file__).parents[3] / "frontend" / ".svelte-kit" / "output" / "client" / ".vite" / "manifest.json"
)


class GameFile(StrEnum):
    MODULE = "module"
    STYLESHEET = "stylesheet"


def url_of(game_file: GameFile) -> str:
    """Where the page build serves the game's chunk or stylesheet; the build names both by a content hash."""
    entry = json.loads(CLIENT_MANIFEST.read_text())[f"src/lib/features/{FEATURE_NAME}/index.ts"]
    path = entry["file"] if game_file is GameFile.MODULE else entry["css"][0]
    return f"/{path}"


def urls_of_the_chunks_of(feature: str) -> set[str]:
    """Where the page build serves a feature's own chunk and every chunk that chunk imports on demand."""
    manifest = json.loads(CLIENT_MANIFEST.read_text())
    entry = manifest[f"src/lib/features/{feature}/index.ts"]
    return {f"/{entry['file']}", *(f"/{manifest[key]['file']}" for key in entry.get("dynamicImports", []))}
