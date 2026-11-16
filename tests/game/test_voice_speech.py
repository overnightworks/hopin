import asyncio
import secrets

import pytest
from logline import Outcome, unit_trace
from speech_client.wire import (
    AUTHORIZATION_HEADER,
    SPEECH_PATH,
    TRACEPARENT_HEADER,
    WORKLOAD_TOKEN_HEADER,
    Language,
    RefusalCode,
    VoiceId,
)

from hopin.agent.speech import SpeechVoice
from hopin.agent.speech_settings import SpeechService
from hopin.game.vocabulary import VOICE_DEADLINE_SECONDS, VOICE_MAX_BYTES
from hopin.game.voice import VOICE_DEADLINE_CUT
from tests.game.support.world import OFFERING_GERMAN_AND_ENGLISH, build_world
from tests.support.log_lines import captured_lines
from tests.support.loopback_origin import ARRIVAL_CAP_SECONDS, Origin
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


def failures_of(lines):
    """The closed reason of each game voice step that ended refused, in order."""
    return [
        line["reason"] for line in lines if line["event"] == "game_voice.ended" and line["outcome"] == Outcome.REFUSED
    ]


def workload_refusals_of(lines):
    return [
        line["reason"]
        for line in lines
        if line["event"] == "workload_token.ended" and line["outcome"] == Outcome.REFUSED
    ]


OUTCOMES = [
    pytest.param(refusal_answer(RefusalCode.FORBIDDEN), "refused", id="refused"),
    pytest.param(refusal_answer(RefusalCode.BUSY, retry_after_seconds=3), "busy", id="busy"),
    pytest.param(chunked_wav(SPOKEN_WAV, terminated=False), "cut", id="cut"),
    pytest.param(chunked_wav(SPOKEN_WAV + bytes(VOICE_MAX_BYTES)), "over_cap", id="over-cap"),
    pytest.param(not_chunked_answer(), "malformed", id="malformed"),
]


@pytest.fixture
def world(sign_in_settings):
    return build_world(sign_in_settings())


@pytest.fixture
def origin():
    origin = Origin()
    yield origin
    origin.close()


def voice_of(world, origin, deadline_seconds=VOICE_DEADLINE_SECONDS, voice_name=VOICE_NAME):
    service = SpeechService(origin.base_url, voice_name, AUDIENCE_SCOPE)
    return SpeechVoice(
        service, deadline_seconds, VOICE_MAX_BYTES, world.relying_party, world.starter_person, world.request_language
    )


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

    async def test_a_line_spoken_inside_a_trace_reaches_the_service_in_that_trace(self, world, origin):
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter()

        trace_id = secrets.token_hex(16)

        with unit_trace(f"00-{trace_id}-{secrets.token_hex(8)}-01"):
            await voice_of(world, origin).speak(starter, TEXT)

        [request] = origin.requests
        assert header_of(request, TRACEPARENT_HEADER).split("-")[1] == trace_id


class TestTheLanguageOfALine:
    @pytest.mark.parametrize("voice_name", [VoiceId.THORSTEN, VoiceId.RYAN])
    async def test_a_line_is_spoken_in_the_language_of_the_starters_page_which_is_german(
        self, world, origin, voice_name
    ):
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter()

        wav = await voice_of(world, origin, voice_name=voice_name).speak(starter, TEXT)

        [request] = origin.requests
        assert (wav, spoken_request(request).language) == (SPOKEN_WAV, Language.DE)

    @pytest.mark.parametrize(("account_language", "spoken"), [("en", Language.EN), (None, Language.DE)])
    async def test_a_line_is_spoken_in_the_starters_language(self, sign_in_settings, origin, account_language, spoken):
        """GM-39: A line is spoken in the starter's language: the account's, else the default."""
        world = build_world(sign_in_settings(), OFFERING_GERMAN_AND_ENGLISH)
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter(account_language=account_language)

        wav = await voice_of(world, origin, voice_name=VoiceId.RYAN).speak(starter, TEXT)

        [request] = origin.requests
        assert (wav, spoken_request(request).language) == (SPOKEN_WAV, spoken)

    async def test_a_starters_language_the_voice_does_not_speak_leaves_the_line_text_only(
        self, sign_in_settings, origin
    ):
        """GM-34: A starter's language the voice does not speak leaves the line text only, before any token."""
        world = build_world(sign_in_settings(), OFFERING_GERMAN_AND_ENGLISH)
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter(account_language="en")

        with captured_lines() as logs:
            wav = await voice_of(world, origin, voice_name=VoiceId.THORSTEN).speak(starter, TEXT)

        assert (wav, failures_of(logs), world.grant_types(), origin.requests) == (
            None,
            ["language_unavailable"],
            [],
            [],
        )


class TestWithoutAVoice:
    @pytest.mark.parametrize(("answer", "logged"), OUTCOMES)
    async def test_an_answer_that_is_no_whole_wav_is_nothing_and_logs_its_outcome_code(
        self, world, origin, answer, logged
    ):
        """GM-34: An answer that is no whole WAV is nothing and logs its outcome code."""
        speaking(origin, answer)
        starter, _ = world.starter()

        with captured_lines() as logs:
            wav = await voice_of(world, origin).speak(starter, TEXT)

        assert (wav, failures_of(logs)) == (None, [logged])

    async def test_a_service_that_is_not_there_is_nothing_and_logs_unreachable(self, world):
        """GM-34: A service that is not there is nothing and logs unreachable."""
        gone = Origin()
        gone.close()
        voice = voice_of(world, gone)
        starter, _ = world.starter()

        with captured_lines() as logs:
            wav = await voice.speak(starter, TEXT)

        assert (wav, failures_of(logs)) == (None, ["unreachable"])

    async def test_an_answer_past_the_deadline_is_nothing_and_logs_past_deadline(self, world, origin):
        """GM-34: An answer past the deadline is nothing and logs past deadline."""
        origin.serve(SPEECH_PATH, chunked_wav(SPOKEN_WAV), held=True)
        starter, _ = world.starter()

        with captured_lines() as logs:
            wav = await voice_of(world, origin, deadline_seconds=0.2).speak(starter, TEXT)

        assert (wav, failures_of(logs)) == (None, ["past_deadline"])

    async def test_a_call_cut_at_the_games_deadline_is_nothing_and_ends_its_one_line_refused_past_deadline(
        self, world, origin
    ):
        """GM-34: A call cut at the game's deadline is nothing and ends its one line refused past deadline."""
        origin.serve(SPEECH_PATH, chunked_wav(SPOKEN_WAV), held=True)
        starter, _ = world.starter()

        with captured_lines() as logs:
            call = asyncio.create_task(voice_of(world, origin).speak(starter, TEXT))
            assert await asyncio.to_thread(origin.received[SPEECH_PATH].wait, ARRIVAL_CAP_SECONDS)
            call.cancel(VOICE_DEADLINE_CUT)
            wav = await call

        ended = [line for line in logs if line["event"] == "game_voice.ended"]
        assert (wav, [(line["outcome"], line.get("reason")) for line in ended]) == (
            None,
            [(Outcome.REFUSED, "past_deadline")],
        )

    async def test_a_call_cancelled_by_the_end_of_its_run_stays_cancelled_and_ends_its_one_line_cancelled(
        self, world, origin
    ):
        """GM-34: A call cancelled by the end of its run stays cancelled and ends its one line cancelled."""
        origin.serve(SPEECH_PATH, chunked_wav(SPOKEN_WAV), held=True)
        starter, _ = world.starter()

        with captured_lines() as logs:
            call = asyncio.create_task(voice_of(world, origin).speak(starter, TEXT))
            assert await asyncio.to_thread(origin.received[SPEECH_PATH].wait, ARRIVAL_CAP_SECONDS)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call

        assert [line["outcome"] for line in logs if line["event"] == "game_voice.ended"] == [Outcome.CANCELLED]

    async def test_a_starter_without_a_standing_session_is_nothing_and_no_workload_token_is_asked_for(
        self, world, origin
    ):
        """GM-34: A starter without a standing session is nothing and no workload token is asked for."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter(with_refresh_token=False)

        with captured_lines() as logs:
            wav = await voice_of(world, origin).speak(starter, TEXT)

        assert (wav, failures_of(logs), world.grant_types(), origin.requests) == (
            None,
            ["starter_token_refused"],
            [],
            [],
        )

    async def test_a_refused_workload_token_is_nothing_and_logs_the_issuers_closed_error(self, world, origin):
        """GM-34: A refused workload token is nothing and logs the issuer's closed error."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        world.issuer.refuses_client_credentials_grants = True
        starter, _ = world.starter()

        with captured_lines() as logs:
            wav = await voice_of(world, origin).speak(starter, TEXT)

        assert (wav, failures_of(logs), workload_refusals_of(logs), origin.requests) == (
            None,
            ["workload_token_refused"],
            ["unauthorized_client"],
            [],
        )

    async def test_a_line_without_a_letter_or_digit_is_nothing_and_never_leaves(self, world, origin):
        """GM-34: A line without a letter or digit is nothing and never leaves."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter()

        with captured_lines() as logs:
            wav = await voice_of(world, origin).speak(starter, "…!?")

        assert (wav, failures_of(logs), origin.requests) == (None, ["input_refused"], [])

    async def test_no_log_holds_the_text_a_token_or_the_audio(self, world, origin):
        """GM-34: No log holds the text, a token or the audio."""
        speaking(origin, chunked_wav(SPOKEN_WAV))
        starter, _ = world.starter()

        with captured_lines() as logs:
            await voice_of(world, origin).speak(starter, TEXT)
            speaking(origin, refusal_answer(RefusalCode.FORBIDDEN))
            await voice_of(world, origin).speak(starter, TEXT)

        secrets_seen = [
            TEXT,
            repr(SPOKEN_WAV),
            *world.issuer.access_tokens_issued("refresh_token"),
            *world.issuer.access_tokens_issued("client_credentials"),
        ]
        rendered = repr(logs)
        assert [secret for secret in secrets_seen if secret in rendered] == []
