import logging
import secrets

import pytest
from agent_runtime_client.config import BackendKind
from pydantic import ValidationError

from hopin.agent.backend import TRANSPORT_LOGGERS, service_backend
from hopin.agent.settings import AgentSettings
from hopin.game.feature import configured_game_feature
from tests.game.support.world import agent_settings

pytestmark = pytest.mark.integration

SERVICE_ENVIRONMENT = {
    "HOPIN_AGENT__BACKEND": BackendKind.SERVICE.value,
    "HOPIN_AGENT__SERVICE_URL": "http://agent-service.test:8080",
    "HOPIN_AGENT__WORKLOAD_SCOPE": "operator-chosen",
}


@pytest.fixture
def agent_environment(monkeypatch):
    for name, value in SERVICE_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_building_the_service_backend_silences_the_transport_loggers():
    loggers = [logging.getLogger(name) for name in TRANSPORT_LOGGERS]
    saved = [logger.filters.copy() for logger in loggers]
    for logger in loggers:
        logger.filters.clear()

    try:
        service_backend(agent_settings())

        for logger in loggers:
            record = logger.makeRecord(logger.name, logging.INFO, __file__, 0, "a token", (), None)
            assert not logger.filter(record)
    finally:
        for logger, filters in zip(loggers, saved, strict=True):
            logger.filters[:] = filters


@pytest.mark.parametrize("scope", ["two scopes", 'quo"te', "back\\slash", "ümlaut", "tail\n"])
def test_a_malformed_workload_scope_fails_when_the_settings_load_and_echoes_no_input(scope, agent_environment):
    agent_environment.setenv("HOPIN_AGENT__WORKLOAD_SCOPE", scope)

    with pytest.raises(ValidationError) as refusal:
        AgentSettings()

    assert "workload_scope" in str(refusal.value)
    assert scope.strip() not in str(refusal.value).replace("workload_scope", "")


@pytest.mark.parametrize("name", ["HOPIN_AGENT__BACKEND", "HOPIN_AGENT__SERVICE_URL", "HOPIN_AGENT__WORKLOAD_SCOPE"])
def test_a_missing_agent_setting_fails_when_the_settings_load(name, agent_environment):
    agent_environment.delenv(name)

    with pytest.raises(ValidationError):
        AgentSettings()


def test_no_agent_settings_at_all_fail_when_the_settings_load(monkeypatch):
    for name in SERVICE_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        AgentSettings()


@pytest.mark.parametrize("backend", ["litellm", "nonsense", ""])
def test_a_backend_the_service_connector_cannot_run_fails_when_the_settings_load(backend, agent_environment):
    agent_environment.setenv("HOPIN_AGENT__BACKEND", backend)

    with pytest.raises(ValidationError):
        AgentSettings()


def test_a_malformed_service_url_fails_without_echoing_it(agent_environment):
    secret_shaped = f"not-a-url-{secrets.token_hex(8)}"
    agent_environment.setenv("HOPIN_AGENT__SERVICE_URL", secret_shaped)

    with pytest.raises(ValidationError) as refusal:
        AgentSettings()

    assert secret_shaped not in str(refusal.value)


def test_the_game_names_its_configured_scope_for_sign_in(agent_environment):
    assert configured_game_feature().sign_in_scopes == ("operator-chosen",)
