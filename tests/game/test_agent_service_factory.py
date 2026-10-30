import logging

import pytest
from pydantic import ValidationError

from hopin.game import GameSettings
from hopin.game.agent_service import TRANSPORT_LOGGERS
from tests.game.support.world import build_world

pytestmark = pytest.mark.integration


def test_building_the_game_master_silences_the_transport_loggers(sign_in_settings):
    loggers = [logging.getLogger(name) for name in TRANSPORT_LOGGERS]
    saved = [logger.filters.copy() for logger in loggers]
    for logger in loggers:
        logger.filters.clear()
    world = build_world(sign_in_settings())

    try:
        world.game_master_over(world.service.transport)

        for logger in loggers:
            record = logger.makeRecord(logger.name, logging.INFO, __file__, 0, "a token", (), None)
            assert not logger.filter(record)
    finally:
        for logger, filters in zip(loggers, saved, strict=True):
            logger.filters[:] = filters


@pytest.mark.parametrize("scope", ["", "two scopes", 'quo"te', "back\\slash", "ümlaut"])
def test_a_malformed_agent_service_scope_fails_when_the_settings_load(scope, monkeypatch):
    monkeypatch.setenv("HOPIN_AGENT_SERVICE_SCOPE", scope)

    with pytest.raises(ValidationError) as refusal:
        GameSettings()

    assert "agent_service_scope" in str(refusal.value)
