from enum import StrEnum

from hopin.game.vocabulary import FEATURE_NAME
from tests.e2e.support.client_manifest import entry_of


class GameFile(StrEnum):
    MODULE = "module"
    STYLESHEET = "stylesheet"


def url_of(game_file: GameFile) -> str:
    """Where the page build serves the game's chunk or stylesheet."""
    entry = entry_of(FEATURE_NAME)
    path = entry["file"] if game_file is GameFile.MODULE else entry["css"][0]
    return f"/{path}"
