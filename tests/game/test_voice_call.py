import secrets

import pytest
from speech_runtime_client.wire import SPEECH_PATH, RefusalCode

from hopin.agent.settings import SpeechSettings
from hopin.config.constants import AUTH_START_PATH, SignalType
from hopin.game.vocabulary import VOICE_PATH, GameRefusal, GameSignal
from tests.game.support.call_world import asked, serving_call
from tests.game.support.fake_agent_backend import FakeAgentBackend, Succeeds
from tests.game.support.speech_origin import AUDIENCE_SCOPE, SPOKEN_WAV, chunked_wav, refusal_answer, speaking
from tests.support.fake_issuer import FakeIssuer
from tests.support.loopback_origin import ARRIVAL_CAP_SECONDS, Origin

pytestmark = pytest.mark.integration

FIRST = "Wie heißt die Hauptstadt von Portugal?"
REVEAL_LINE = "Lissabon."
SECOND = "Wie heißt die Hauptstadt von Spanien?"
VOICE_TYPE = GameSignal.GAME_VOICE.value


@pytest.fixture
def origin():
    origin = Origin()
    speaking(origin, chunked_wav(SPOKEN_WAV))
    yield origin
    origin.close()


@pytest.fixture
def speech(origin):
    return SpeechSettings(origin=origin.base_url, voice="piper", audience_scope=AUDIENCE_SCOPE)


@pytest.fixture
def backend():
    backend = FakeAgentBackend()
    backend.answer_next(Succeeds(FIRST), Succeeds(REVEAL_LINE), Succeeds(SECOND))
    return backend


@pytest.fixture
def call(sign_in_settings, backend, speech):
    sign_in = sign_in_settings()
    with serving_call(sign_in, FakeIssuer(sign_in), backend, speech) as serving:
        yield serving


def voice_path_of(frame):
    assert frame["type"] == VOICE_TYPE
    return frame["path"]


def types_of(frames):
    return [frame["type"] for frame in frames]


class TestTheVoiceInARealCall:
    def test_both_seats_are_sent_the_voice_after_the_question_and_fetch_the_same_wav_only_with_a_session(self, call):
        """GM-32: Both seats are sent the voice after the question and fetch the same wav only with a session."""
        host, guest, guest_member = call.host_and_guest()

        guest.start()

        [host_frames, guest_frames] = [seat.frames(4) for seat in (host, guest)]
        assert host_frames[:3] == asked(guest, FIRST, voiced=True)
        assert voice_path_of(host_frames[3]) == voice_path_of(guest_frames[3])
        fetched = call.voice(guest_member, voice_path_of(guest_frames[3]))
        assert (fetched.status_code, fetched.content, fetched.headers["content-type"]) == (
            200,
            SPOKEN_WAV,
            "audio/wav",
        )
        assert fetched.headers["cache-control"] == "no-store"
        without_session = call.client.get(voice_path_of(guest_frames[3]), headers={"accept": "*/*"})
        assert without_session.status_code == 401

    def test_a_reveal_chains_into_the_next_question_through_the_voice_on_grants_the_issuer_honours_once(self, call):
        """GM-33: A reveal chains into the next question through the voice on grants the issuer honours once."""
        host, guest, _ = call.host_and_guest()
        guest.start()
        guest.frames(4)
        host.frames(4)

        guest.reveal()
        frames = guest.frames(7)

        assert types_of(frames) == [
            "game_phase",
            "game_line",
            VOICE_TYPE,
            "game_phase",
            "game_line",
            "game_phase",
            VOICE_TYPE,
        ]
        assert GameRefusal.UNAVAILABLE.value not in repr(frames)

    def test_the_voice_of_a_line_is_replaced_by_the_next_and_gone_with_the_call(self, call):
        """GM-32: The voice of a line is replaced by the next and gone with the call."""
        host, guest, guest_member = call.host_and_guest()
        guest.start()
        first_path = voice_path_of(guest.frames(4)[3])
        host.frames(4)
        guest.reveal()
        second_path = voice_path_of(guest.frames(7)[2])

        replaced = call.voice(guest_member, first_path)
        host.send({"type": SignalType.END_ROOM.value})
        gone = call.voice(guest_member, second_path)

        assert (replaced.status_code, gone.status_code) == (404, 404)

    def test_a_voice_that_is_no_id_of_a_voice_is_not_found(self, call):
        """GM-32: A voice that is no id of a voice is not found."""
        _, _, guest_member = call.host_and_guest()

        assert [
            call.voice(guest_member, f"{VOICE_PATH}/{voice_id}").status_code
            for voice_id in ("short", secrets.token_urlsafe(16))
        ] == [
            404,
            404,
        ]


class TestWithoutTheVoice:
    def test_without_speech_configured_a_line_is_text_only_and_no_workload_token_is_asked_for(
        self, sign_in_settings, backend, origin
    ):
        """GM-34: Without speech configured a line is text only and no workload token is asked for."""
        sign_in = sign_in_settings()
        issuer = FakeIssuer(sign_in)
        with serving_call(sign_in, issuer, backend) as call:
            _, guest, _ = call.host_and_guest()

            guest.start()
            assert guest.frames(3) == asked(guest, FIRST)
            guest.answer("Lissabon")

        assert (call.grant_types(), origin.requests) == (["refresh_token"], [])

    @pytest.mark.parametrize(
        "answer",
        [refusal_answer(RefusalCode.BUSY, retry_after_seconds=2), refusal_answer(RefusalCode.FORBIDDEN)],
        ids=["busy", "refused"],
    )
    def test_a_service_that_refuses_leaves_the_line_as_text_and_the_quiz_goes_on(self, call, origin, answer):
        """GM-34: A service that refuses leaves the line as text and the quiz goes on."""
        speaking(origin, answer)
        _host, guest, _ = call.host_and_guest()

        guest.start()
        assert guest.frames(4) == [*asked(guest, FIRST, voiced=True), {"type": VOICE_TYPE}]
        guest.answer("Lissabon")

        assert guest.frames(1)[0]["type"] == GameSignal.GAME_ANSWER_COUNT.value
        assert origin.received[SPEECH_PATH].wait(ARRIVAL_CAP_SECONDS)


class TestSigningInForTheVoice:
    def test_the_sign_in_asks_for_the_audience_scope_only_with_speech_configured(
        self, sign_in_settings, backend, speech
    ):
        """GM-32: The sign-in asks for the audience scope only with speech configured."""
        sign_in = sign_in_settings()
        scopes = []
        for configured in (speech, None):
            with serving_call(sign_in, FakeIssuer(sign_in), backend, configured) as call:
                location = call.client.get(AUTH_START_PATH).headers["location"]
                scopes.append(AUDIENCE_SCOPE in location)

        assert scopes == [True, False]
