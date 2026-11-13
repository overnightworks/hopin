import secrets

import pytest
import structlog.testing
from speech_client.wire import (
    AUTHORIZATION_HEADER,
    SPEECH_PATH,
    WORKLOAD_TOKEN_HEADER,
    RefusalCode,
    VoiceId,
)

from hopin.agent.backend import StarterPerson
from hopin.agent.speech import SpeechVoice
from hopin.agent.speech_settings import SpeechService
from hopin.game.vocabulary import VOICE_DEADLINE_SECONDS, VOICE_MAX_BYTES
from tests.game.support.world import build_world
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import (
    AUDIENCE_SCOPE,
    SPOKEN_WAV,
    chunked_wav,
    header_of,
    not_chunked_answer,
    refusal_answer,
    speaking,
    spoken_request,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

TEXT = f"Wie heißt die Hauptstadt {secrets.token_hex(8)}?"
VOICE_NAME = VoiceId.THORSTEN
FAILED = "game_voice_failed"
OUTCOMES = [
    pytest.param(refusal_answer(RefusalCode.FORBIDDEN), {"outcome": "refused", "code": "forbidden"}, id="refused"),
    pytest.param(refusal_answer(RefusalCode.BUSY, retry_after_seconds=3), {"outcome": "busy"}, id="busy"),
    pytest.param(chunked_wav(SPOKEN_WAV, terminated=False), {"outcome": "cut"}, id="cut"),
    pytest.param(chunked_wav(SPOKEN_WAV + bytes(VOICE_MAX_BYTES)), {"outcome": "over_cap"}, id="over-cap"),
    pytest.param(not_chunked_answer(), {"outcome": "malformed"}, id="malformed"),
]


@pytest.fixture
def world(sign_in_settings):
    return build_world(sign_in_settings())


@pytest.fixture
def origin():
    origin = Origin()
    yield origin
    origin.close()


def voice_of(world, origin, deadline_seconds=VOICE_DEADLINE_SECONDS):
    person = StarterPerson(world.relying_party, world.sessions, world.web_auth)
    service = SpeechService(origin.base_url, VOICE_NAME, AUDIENCE_SCOPE)
    return SpeechVoice(service, deadline_seconds, VOICE_MAX_BYTES, world.relying_party, person)


def failures_in(logs):
    return [
        {key: value for key, value in log.items() if key not in {"event", "log_level"}}
        for log in logs
        if log["event"] == FAILED
    ]


class TestSpeaking:
    async def test_a_line_goes_to_the_service_on_the_starters_token_and_the_workload_token_and_comes_back_whole(
        self, world, origin
    ):
        """GM-21: A line goes to the service on the starter's token and the workload token and comes back whole."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter()

        wav = await voice_of(world, origin).speak(starter, TEXT)

        [request] = origin.requests
        assert wav == SPOKEN_WAV
        assert (request[0], request[1]) == ("POST", SPEECH_PATH)
        assert (spoken_request(request).input, spoken_request(request).voice) == (TEXT, VOICE_NAME)
        assert (
            header_of(request, AUTHORIZATION_HEADER)
            == f"Bearer {world.issuer.access_tokens_issued('refresh_token')[0]}"
        )
        assert header_of(request, WORKLOAD_TOKEN_HEADER) == world.issuer.access_tokens_issued("client_credentials")[0]

    async def test_it_asks_the_issuer_for_one_refresh_grant_and_one_workload_grant_for_the_audience_scope(
        self, world, origin
    ):
        """GM-21: It asks the issuer for one refresh grant and one workload grant for the audience scope."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter()

        await voice_of(world, origin).speak(starter, TEXT)

        assert world.grant_types() == ["refresh_token", "client_credentials"]
        workload_grant = world.issuer.exchanges[1][0]
        assert workload_grant.form["scope"] == AUDIENCE_SCOPE


class TestWithoutAVoice:
    @pytest.mark.parametrize(("answer", "logged"), OUTCOMES)
    async def test_an_answer_that_is_no_whole_wav_is_nothing_and_logs_its_outcome_code(
        self, world, origin, answer, logged
    ):
        """GM-34: An answer that is no whole WAV is nothing and logs its outcome code."""
        speaking(origin, answer)
        starter, _ = world.starter()

        with structlog.testing.capture_logs() as logs:
            wav = await voice_of(world, origin).speak(starter, TEXT)

        assert (wav, failures_in(logs)) == (None, [logged])

    async def test_a_service_that_is_not_there_is_nothing_and_logs_unreachable(self, world):
        """GM-34: A service that is not there is nothing and logs unreachable."""
        gone = Origin()
        gone.close()
        voice = voice_of(world, gone)
        starter, _ = world.starter()

        with structlog.testing.capture_logs() as logs:
            wav = await voice.speak(starter, TEXT)

        assert (wav, failures_in(logs)) == (None, [{"outcome": "unreachable"}])

    async def test_an_answer_past_the_deadline_is_nothing_and_logs_past_deadline(self, world, origin):
        """GM-34: An answer past the deadline is nothing and logs past deadline."""
        origin.serve(SPEECH_PATH, chunked_wav(SPOKEN_WAV), held=True)
        starter, _ = world.starter()

        with structlog.testing.capture_logs() as logs:
            wav = await voice_of(world, origin, deadline_seconds=0.2).speak(starter, TEXT)

        assert (wav, failures_in(logs)) == (None, [{"outcome": "past_deadline"}])

    async def test_a_starter_without_a_standing_session_is_nothing_and_no_workload_token_is_asked_for(
        self, world, origin
    ):
        """GM-34: A starter without a standing session is nothing and no workload token is asked for."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter(with_refresh_token=False)

        with structlog.testing.capture_logs() as logs:
            wav = await voice_of(world, origin).speak(starter, TEXT)

        assert (wav, failures_in(logs), world.grant_types(), origin.requests) == (
            None,
            [{"outcome": "starter_token_refused"}],
            [],
            [],
        )

    async def test_a_refused_workload_token_is_nothing_and_logs_the_issuers_closed_error(self, world, origin):
        """GM-34: A refused workload token is nothing and logs the issuer's closed error."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        world.issuer.refuses_client_credentials_grants = True
        starter, _ = world.starter()

        with structlog.testing.capture_logs() as logs:
            wav = await voice_of(world, origin).speak(starter, TEXT)

        assert (wav, failures_in(logs), origin.requests) == (
            None,
            [{"outcome": "workload_token_refused", "idp_error": "unauthorized_client"}],
            [],
        )

    async def test_a_line_without_a_letter_or_digit_is_nothing_and_never_leaves(self, world, origin):
        """GM-34: A line without a letter or digit is nothing and never leaves."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter()

        with structlog.testing.capture_logs() as logs:
            wav = await voice_of(world, origin).speak(starter, "…!?")

        assert (wav, failures_in(logs), origin.requests) == (None, [{"outcome": "input_refused"}], [])

    async def test_no_log_holds_the_text_a_token_or_the_audio(self, world, origin):
        """GM-34: No log holds the text, a token or the audio."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter()

        with structlog.testing.capture_logs() as logs:
            await voice_of(world, origin).speak(starter, TEXT)
            speaking(origin, refusal_answer(RefusalCode.FORBIDDEN))
            await voice_of(world, origin).speak(starter, TEXT)

        secrets_seen = [
            TEXT,
            SPOKEN_WAV.decode(),
            *world.issuer.access_tokens_issued("refresh_token"),
            *world.issuer.access_tokens_issued("client_credentials"),
        ]
        rendered = repr(logs)
        assert [secret for secret in secrets_seen if secret in rendered] == []
