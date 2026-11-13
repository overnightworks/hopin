import secrets

import pytest
from pydantic import ValidationError
from speech_client.wire import VoiceId

from hopin.agent.speech_settings import SpeechSettings
from tests.support.settings_refusal import assert_refused_without_echo

pytestmark = pytest.mark.integration

SPEECH_ENVIRONMENT = {
    "HOPIN_SPEECH__ORIGIN": "http://speech.test:8090",
    "HOPIN_SPEECH__VOICE": VoiceId.NORMAN.value,
    "HOPIN_SPEECH__AUDIENCE_SCOPE": "scenario-audience",
}
SECRET_SHAPED = f"not-a-value-{secrets.token_hex(8)}"
MALFORMED_SETTINGS = [
    pytest.param("HOPIN_SPEECH__ORIGIN", f"http://speech.test:8090/{SECRET_SHAPED}", id="an-origin-with-a-path"),
    pytest.param("HOPIN_SPEECH__ORIGIN", f"https://{SECRET_SHAPED}.test", id="an-origin-over-https"),
    pytest.param("HOPIN_SPEECH__ORIGIN", f"http://speech.test:{SECRET_SHAPED}", id="an-origin-with-a-bad-port"),
    pytest.param("HOPIN_SPEECH__VOICE", "", id="an-empty-voice"),
    pytest.param("HOPIN_SPEECH__VOICE", f"not-a-catalogue-voice-{SECRET_SHAPED}", id="a-voice-outside-the-catalogue"),
    pytest.param("HOPIN_SPEECH__AUDIENCE_SCOPE", f"two {SECRET_SHAPED}", id="a-scope-with-a-space"),
    pytest.param("HOPIN_SPEECH__AUDIENCE_SCOPE", "", id="an-empty-scope"),
]


@pytest.fixture
def speech_environment(monkeypatch):
    for name, value in SPEECH_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def test_the_three_speech_settings_load_from_their_environment(speech_environment):
    speech = SpeechSettings()

    assert (speech.origin, speech.voice, speech.audience_scope) == (
        SPEECH_ENVIRONMENT["HOPIN_SPEECH__ORIGIN"],
        SPEECH_ENVIRONMENT["HOPIN_SPEECH__VOICE"],
        SPEECH_ENVIRONMENT["HOPIN_SPEECH__AUDIENCE_SCOPE"],
    )


def test_without_an_origin_the_voice_is_off_and_its_voice_has_a_default(speech_environment):
    for name in SPEECH_ENVIRONMENT:
        speech_environment.delenv(name)

    speech = SpeechSettings()

    assert (speech.origin, speech.audience_scope, bool(speech.voice), speech.service) == (None, None, True, None)


def test_an_origin_without_an_audience_scope_fails_when_the_settings_load(speech_environment):
    speech_environment.delenv("HOPIN_SPEECH__AUDIENCE_SCOPE")

    with pytest.raises(ValidationError):
        SpeechSettings()


@pytest.mark.parametrize(("name", "value"), MALFORMED_SETTINGS)
def test_a_malformed_speech_setting_fails_when_the_settings_load_and_echoes_no_input(name, value, speech_environment):
    speech_environment.setenv(name, value)

    assert_refused_without_echo(SpeechSettings, SECRET_SHAPED)


def test_an_audience_scope_without_an_origin_leaves_the_voice_off(speech_environment):
    speech_environment.delenv("HOPIN_SPEECH__ORIGIN")

    assert SpeechSettings(audience_scope="scenario-audience").service is None


def test_hearing_is_off_by_default_and_names_no_service(speech_environment):
    speech = SpeechSettings()

    assert (speech.hearing, speech.hearing_service) == (False, None)


def test_hearing_on_names_the_configured_service(speech_environment):
    speech_environment.setenv("HOPIN_SPEECH__HEARING", "true")

    speech = SpeechSettings()

    assert (speech.hearing, speech.hearing_service) == (True, speech.service)
    assert speech.hearing_service is not None


@pytest.mark.parametrize("missing", ["HOPIN_SPEECH__ORIGIN", "HOPIN_SPEECH__AUDIENCE_SCOPE"])
def test_hearing_on_without_an_origin_and_an_audience_scope_fails_when_the_settings_load(speech_environment, missing):
    speech_environment.setenv("HOPIN_SPEECH__HEARING", "true")
    speech_environment.delenv(missing)

    with pytest.raises(ValidationError):
        SpeechSettings()


def test_speaking_is_off_by_default_and_names_no_service(speech_environment):
    speech = SpeechSettings()

    assert (speech.speaking, speech.speaking_service) == (False, None)


def test_speaking_on_names_the_configured_service(speech_environment):
    speech_environment.setenv("HOPIN_SPEECH__SPEAKING", "true")

    speech = SpeechSettings()

    assert (speech.speaking, speech.speaking_service) == (True, speech.service)
    assert speech.speaking_service is not None


@pytest.mark.parametrize("missing", ["HOPIN_SPEECH__ORIGIN", "HOPIN_SPEECH__AUDIENCE_SCOPE"])
def test_speaking_on_without_an_origin_and_an_audience_scope_fails_when_the_settings_load(speech_environment, missing):
    speech_environment.setenv("HOPIN_SPEECH__SPEAKING", "true")
    speech_environment.delenv(missing)

    with pytest.raises(ValidationError):
        SpeechSettings()
