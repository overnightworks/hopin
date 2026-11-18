"""The game's agent settings a process needs to start: the gateway is named, never reached."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

GAME_ENVIRONMENT = {
    "HOPIN_AGENT__GATEWAY__ORIGIN": "http://gateway.test:4000",
    "HOPIN_AGENT__GATEWAY__KEY_FILE": "/nonexistent/gateway-key",
    "HOPIN_AGENT__API_GROUP": "/services/scenario/api",
}


def set_game_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in GAME_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
