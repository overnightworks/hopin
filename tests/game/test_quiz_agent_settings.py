import secrets

import pytest
from agents.agent_file import load_agent
from agents.config import BackendKind
from pydantic import ValidationError
from speech_client.wire import VoiceId

from hopin.agent.backend import QUIZ_AGENT_FILE
from hopin.agent.feature import GameFeature, configured_game_feature
from hopin.agent.settings import AgentSettings, SpeechSettings
from hopin.game.vocabulary import GAME_RUN_DEADLINE_SECONDS, VOICE_DEADLINE_SECONDS

pytestmark = pytest.mark.integration

GATEWAY_ENVIRONMENT = {
    "HOPIN_AGENT__BACKEND": BackendKind.LITELLM.value,
    "HOPIN_AGENT__GATEWAY__ORIGIN": "http://gateway.test:4000",
    "HOPIN_AGENT__GATEWAY__KEY_FILE": "/run/secrets/scenario-key",
    "HOPIN_AGENT__API_GROUP": "/services/scenario/api",
}
SPEECH_ENVIRONMENT = {
    "HOPIN_SPEECH__ORIGIN": "http://speech.test:8090",
    "HOPIN_SPEECH__VOICE": VoiceId.NORMAN.value,
    "HOPIN_SPEECH__AUDIENCE_SCOPE": "scenario-audience",
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
    pytest.param("HOPIN_SPEECH__ORIGIN", f"http://speech.test:8090/{SECRET_SHAPED}", id="a-speech-origin-with-a-path"),
    pytest.param("HOPIN_SPEECH__ORIGIN", f"https://{SECRET_SHAPED}.test", id="a-speech-origin-over-https"),
    pytest.param("HOPIN_SPEECH__ORIGIN", f"http://speech.test:{SECRET_SHAPED}", id="a-speech-origin-with-a-bad-port"),
    pytest.param("HOPIN_SPEECH__VOICE", "", id="an-empty-voice"),
    pytest.param("HOPIN_SPEECH__VOICE", f"not-a-catalogue-voice-{SECRET_SHAPED}", id="a-voice-outside-the-catalogue"),
    pytest.param("HOPIN_SPEECH__AUDIENCE_SCOPE", f"two {SECRET_SHAPED}", id="a-scope-with-a-space"),
    pytest.param("HOPIN_SPEECH__AUDIENCE_SCOPE", "", id="an-empty-scope"),
]


@pytest.fixture
def agent_environment(monkeypatch):
    for name, value in {**GATEWAY_ENVIRONMENT, **SPEECH_ENVIRONMENT}.items():
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


def test_the_quiz_agent_file_and_a_voice_wait_end_inside_the_tables_deadline():
    agent = load_agent(QUIZ_AGENT_FILE, [])

    assert agent.max_duration_seconds + VOICE_DEADLINE_SECONDS < GAME_RUN_DEADLINE_SECONDS


def test_the_three_speech_settings_load_as_the_speech_section(agent_environment):
    speech = AgentSettings().speech

    assert (speech.origin, speech.voice, speech.audience_scope) == (
        SPEECH_ENVIRONMENT["HOPIN_SPEECH__ORIGIN"],
        SPEECH_ENVIRONMENT["HOPIN_SPEECH__VOICE"],
        SPEECH_ENVIRONMENT["HOPIN_SPEECH__AUDIENCE_SCOPE"],
    )


def test_without_an_origin_the_voice_is_off_and_its_voice_has_a_default(agent_environment):
    for name in SPEECH_ENVIRONMENT:
        agent_environment.delenv(name)

    speech = AgentSettings().speech

    assert (speech.origin, speech.audience_scope, bool(speech.voice)) == (None, None, True)


def test_an_origin_without_an_audience_scope_fails_when_the_settings_load(agent_environment):
    agent_environment.delenv("HOPIN_SPEECH__AUDIENCE_SCOPE")

    with pytest.raises(ValidationError):
        AgentSettings()


def test_the_sign_in_asks_for_no_audience_scope_without_speech_and_for_the_configured_one_with_it():
    speech = SpeechSettings(origin="http://speech.test:8090", audience_scope="scenario-audience")

    assert (GameFeature(None).sign_in_scopes, GameFeature(None, speech).sign_in_scopes) == ((), ("scenario-audience",))


def test_an_audience_scope_without_an_origin_leaves_the_sign_in_asking_for_no_scope_and_the_voice_off():
    speech = SpeechSettings(audience_scope="scenario-audience")

    assert (GameFeature(None, speech).sign_in_scopes, speech.service) == ((), None)
