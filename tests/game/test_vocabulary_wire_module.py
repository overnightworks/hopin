import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.config.constants import SignalType
from hopin.game.vocabulary import GamePhase, GameRefusal, GameSignal
from hopin.web.routes import create_router
from hopin.web.wire import WIRE_MODULE_PATH
from tests.support.wire_module import served_exports

pytestmark = pytest.mark.integration


def members(vocabulary):
    return {member.name: member.value for member in vocabulary}


def test_the_wire_module_carries_the_games_signal_types_phases_and_refusals(handler, room_manager):
    app = FastAPI()
    app.include_router(create_router(handler, room_manager))

    exports = served_exports(TestClient(app).get(WIRE_MODULE_PATH).text)

    assert exports["SIGNAL_TYPE"] == {**members(SignalType), **members(GameSignal)}
    assert exports["GAME_PHASE"] == members(GamePhase)
    assert exports["GAME_REFUSAL"] == members(GameRefusal)
