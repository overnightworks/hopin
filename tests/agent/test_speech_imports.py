from pathlib import Path

import pytest

from tests.support.imported_modules import imported_modules, names

SOURCE = Path(__file__).resolve().parents[2] / "src" / "hopin"
SPEECH_CLIENT = "speech_client"
# The speech client's voice type is the setting's type, the speak call is the renderer of the game's voice and of the
# agent's speaking, and the hearing route calls the finished-utterance helper; the renderer stays once the game is
# removed.
SPEECH_MODULES = {
    SOURCE / "agent" / "speaking.py",
    SOURCE / "agent" / "speech_settings.py",
    SOURCE / "agent" / "hearing_route.py",
}


@pytest.mark.parametrize("path", sorted(SOURCE.rglob("*.py")), ids=lambda path: str(path.relative_to(SOURCE)))
def test_only_the_speech_connector_names_the_speech_client(path):
    named = {module for module in imported_modules(path) if names(module, SPEECH_CLIENT)}

    assert bool(named) == (path in SPEECH_MODULES)
