import secrets

import pytest
from agent_runtime_client.agents import load_agent
from agent_runtime_client.config import BackendKind
from pydantic import ValidationError

from hopin.agent.backend import QUIZ_AGENT_FILE
from hopin.agent.feature import configured_game_feature
from hopin.agent.settings import AgentSettings
from hopin.agent.tools import TOOLS
from hopin.game.vocabulary import GAME_RUN_DEADLINE_SECONDS

pytestmark = pytest.mark.integration

GATEWAY_ENVIRONMENT = {
    "HOPIN_AGENT__BACKEND": BackendKind.LITELLM.value,
    "HOPIN_AGENT__GATEWAY__ORIGIN": "http://gateway.test:4000",
    "HOPIN_AGENT__GATEWAY__KEY_FILE": "/run/secrets/scenario-key",
    "HOPIN_AGENT__API_GROUP": "/services/scenario/api",
}
CA_FILE = "HOPIN_AGENT__GATEWAY__CA_FILE"
SECRET_SHAPED = f"not-a-value-{secrets.token_hex(8)}"
MALFORMED_SETTINGS = [
    pytest.param("HOPIN_AGENT__BACKEND", SECRET_SHAPED, id="a-nonsense-backend"),
    pytest.param(
        "HOPIN_AGENT__GATEWAY__ORIGIN", f"http://gateway.test:4000/{SECRET_SHAPED}", id="an-origin-with-a-path"
    ),
    pytest.param("HOPIN_AGENT__GATEWAY__ORIGIN", f"https://{SECRET_SHAPED}.test", id="https-without-a-ca-file"),
    pytest.param(CA_FILE, f"/run/secrets/{SECRET_SHAPED}", id="http-with-a-ca-file"),
    pytest.param("HOPIN_AGENT__GATEWAY__KEY_FILE", f"secrets/{SECRET_SHAPED}", id="a-relative-key-file"),
    pytest.param("HOPIN_AGENT__API_GROUP", "", id="an-empty-group"),
]


@pytest.fixture
def agent_environment(monkeypatch):
    for name, value in GATEWAY_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_the_four_settings_load_as_the_clients_agent_section(agent_environment):
    agent = AgentSettings().agent

    assert agent.backend is BackendKind.LITELLM
    assert agent.gateway.origin == GATEWAY_ENVIRONMENT["HOPIN_AGENT__GATEWAY__ORIGIN"]
    assert str(agent.gateway.key_file) == GATEWAY_ENVIRONMENT["HOPIN_AGENT__GATEWAY__KEY_FILE"]
    assert agent.api_group == GATEWAY_ENVIRONMENT["HOPIN_AGENT__API_GROUP"]


def test_a_missing_key_file_does_not_refuse_the_start(agent_environment):
    assert configured_game_feature().backend is not None


@pytest.mark.parametrize("name", GATEWAY_ENVIRONMENT)
def test_a_missing_agent_setting_fails_when_the_settings_load(name, agent_environment):
    agent_environment.delenv(name)

    with pytest.raises(ValidationError):
        AgentSettings()


def test_no_agent_settings_at_all_fail_when_the_settings_load(monkeypatch):
    for name in GATEWAY_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError):
        AgentSettings()


@pytest.mark.parametrize(("name", "value"), MALFORMED_SETTINGS)
def test_a_malformed_agent_setting_fails_when_the_settings_load_and_echoes_no_input(name, value, agent_environment):
    agent_environment.setenv(name, value)

    with pytest.raises(ValidationError) as refusal:
        AgentSettings()

    assert SECRET_SHAPED not in str(refusal.value)


def test_the_quiz_agent_file_ends_inside_the_tables_deadline():
    agent = load_agent(QUIZ_AGENT_FILE, list(TOOLS))

    assert agent.max_duration_seconds < GAME_RUN_DEADLINE_SECONDS
