"""CALL-60: the agent's `speak` at HopIn's real `/mcp` and `/ws`, with a fake speech service on a loopback port, a fake
issuer, and the sound fetched at the route the seats are told."""

import asyncio
import logging
import secrets
from functools import partial

import pytest
import structlog.testing
from agents.member import LEASE_SECONDS, SPEAK_TOOL, PlaceRef
from speech_client.wire import (
    AUTHORIZATION_HEADER,
    SPEECH_PATH,
    WORKLOAD_TOKEN_HEADER,
    RefusalCode,
    VoiceId,
)

from hopin.agent.speaking import SPEAK_MAX_BYTES
from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import CALL_KIND, JOIN_CALL, SPEAK_MAX_CHARACTERS
from hopin.participation.feature import Participation
from hopin.participation.vocabulary import VOICE_PATH, ParticipationSignal
from tests.support.dialed_call import join_arguments
from tests.support.fake_issuer import TOKEN_EXCHANGE_GRANT
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import (
    AUDIENCE_SCOPE,
    SPOKEN_WAV,
    chunked_wav,
    failures_in,
    header_of,
    not_chunked_answer,
    refusal_answer,
    silent_wav,
    speaking,
    spoken_request,
    with_sample_rate,
)

pytestmark = pytest.mark.integration

VOICE = ParticipationSignal.AGENT_VOICE.value
LINE = ParticipationSignal.AGENT_LINE.value
SENTENCE_SECONDS = 1.5
SENTENCE_MS = 1500
SOUND = silent_wav(SENTENCE_SECONDS)
CLIENT_CREDENTIALS_GRANT = "client_credentials"
TEXT = "Ein geheimer Satz"
FAILED = "agent_speak_failed"
failures_of = partial(failures_in, event=FAILED)


@pytest.fixture
def speech_origin():
    origin = Origin()
    speaking(origin, chunked_wav(SOUND))
    yield origin
    origin.close()


@pytest.fixture
def speaking_service(speech_origin):
    return SpeechService(speech_origin.base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE)


def speak(scene, text=TEXT, room_id=None):
    place = PlaceRef(kind=CALL_KIND, id=scene.owner.room_id if room_id is None else room_id)
    return scene.call.call(scene.token, SPEAK_TOOL, {"place": place.model_dump(), "text": text})


def refusal_of(dialed):
    return (dialed.is_error, dialed.outcome)


def drained(scene):
    """Wait until both seats have read what the server sent before the host's lock, which reaches them both."""
    scene.owner.send({"type": "lock_room"})
    for seat in (scene.owner, scene.member):
        seat.frame_of_type("room_locked")


def frames_of(seat, signal_type):
    return [frame for frame in seat.received if frame["type"] == signal_type]


def sound_at(scene, seat, login=None):
    voice_id = seat.frame_of_type(VOICE)["voice_id"]
    return scene.call.voice(scene.member_login if login is None else login, f"{VOICE_PATH}/{voice_id}")


class TestSpeakingOff:
    @pytest.fixture
    def speaking_service(self):
        return None

    def test_call_60_with_the_setting_off_speak_is_unavailable_logs_speaking_off_and_nobody_is_called(
        self, joined_agent
    ):
        with structlog.testing.capture_logs() as logs:
            dialed = speak(joined_agent)
        drained(joined_agent)

        issuer = joined_agent.call.issuer
        assert refusal_of(dialed) == (True, {"refused": "unavailable"})
        assert failures_of(logs) == [{"outcome": "speaking_off"}]
        assert (
            issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT),
            issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT),
            [frames_of(seat, VOICE) + frames_of(seat, LINE) for seat in (joined_agent.owner, joined_agent.member)],
        ) == ([], [], [[], []])


class TestSpeaking:
    @pytest.fixture
    def spoken(self, joined_agent):
        return speak(joined_agent)

    def test_call_60_it_answers_spoken_with_the_length_of_the_sound(self, spoken):
        assert (spoken.is_error, spoken.outcome) == (False, {"duration_ms": SENTENCE_MS})

    def test_call_60_every_seat_gets_one_voice_and_the_sound_is_served_no_store_to_a_seat_of_the_room(
        self, joined_agent, spoken
    ):
        for seat in (joined_agent.owner, joined_agent.member):
            fetched = sound_at(joined_agent, seat, joined_agent.member_login)

            assert (fetched.status_code, fetched.content, fetched.headers["cache-control"]) == (200, SOUND, "no-store")
        drained(joined_agent)
        assert [len(frames_of(seat, VOICE)) for seat in (joined_agent.owner, joined_agent.member)] == [1, 1]

    def test_call_60_it_stands_in_the_agents_window_as_own_and_spoken_and_once_in_the_chat(self, joined_agent, spoken):
        drained(joined_agent)

        [entry] = joined_agent.entries()
        assert (entry["kind"], entry["text"], entry["own"], entry["spoken"]) == ("agent", TEXT, True, True)
        assert [len(frames_of(seat, LINE)) for seat in (joined_agent.owner, joined_agent.member)] == [1, 1]

    def test_call_60_the_sentence_goes_to_speech_on_the_agents_held_token_and_hopins_workload_token(
        self, joined_agent, spoken, speech_origin
    ):
        issuer = joined_agent.call.issuer

        [request] = speech_origin.requests
        [held] = issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)
        [workload] = issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT)
        assert (
            request[:2],
            spoken_request(request).input,
            header_of(request, AUTHORIZATION_HEADER),
            header_of(request, WORKLOAD_TOKEN_HEADER),
        ) == (("POST", SPEECH_PATH), TEXT, f"Bearer {held}", workload)

    def test_call_60_a_sentence_of_the_greatest_length_is_spoken(self, joined_agent):
        dialed = speak(joined_agent, "x" * SPEAK_MAX_CHARACTERS)

        assert dialed.is_error is False


class TestTheVoiceRoute:
    @pytest.fixture
    def path(self, joined_agent):
        speak(joined_agent)
        return f"{VOICE_PATH}/{joined_agent.member.frame_of_type(VOICE)['voice_id']}"

    def test_call_60_a_signed_in_person_with_no_seat_in_the_room_gets_nothing(self, joined_agent, path):
        [_, stranger] = joined_agent.call.member()

        assert joined_agent.call.voice(stranger, path).status_code == 404

    def test_call_60_without_a_session_the_gate_answers_before_the_route(self, joined_agent, path):
        assert joined_agent.call.client.get(path, headers={"accept": "*/*"}).status_code == 401

    def test_call_60_the_seat_check_runs_on_the_event_loop_which_changes_the_seats(
        self, joined_agent, path, monkeypatch
    ):
        runs_in_the_loop = []
        holds_seat = Participation.holds_seat

        def recording(participation, account_id, room_id):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                runs_in_the_loop.append(False)
            else:
                runs_in_the_loop.append(True)
            return holds_seat(participation, account_id, room_id)

        monkeypatch.setattr(Participation, "holds_seat", recording)

        fetched = joined_agent.call.voice(joined_agent.member_login, path)

        assert (fetched.status_code, runs_in_the_loop) == (200, [True])

    def test_call_60_an_id_nobody_was_told_gets_nothing(self, joined_agent, path):
        unknown = f"{VOICE_PATH}/{secrets.token_urlsafe(16)}"

        assert joined_agent.call.voice(joined_agent.member_login, unknown).status_code == 404


UNREADABLE = "unreadable_audio"
SPEECH_FAILS = [
    pytest.param(refusal_answer(RefusalCode.FORBIDDEN), {"outcome": "refused", "code": "forbidden"}, id="refused"),
    pytest.param(refusal_answer(RefusalCode.BUSY, retry_after_seconds=3), {"outcome": "busy"}, id="busy"),
    pytest.param(chunked_wav(SOUND, terminated=False), {"outcome": "cut"}, id="cut"),
    pytest.param(chunked_wav(SOUND + bytes(SPEAK_MAX_BYTES)), {"outcome": "over_cap"}, id="over-cap"),
    pytest.param(not_chunked_answer(), {"outcome": "malformed"}, id="malformed"),
    pytest.param(chunked_wav(SPOKEN_WAV), {"outcome": UNREADABLE}, id="unreadable-sound"),
    pytest.param(chunked_wav(with_sample_rate(SOUND, 0)), {"outcome": UNREADABLE}, id="zero-sample-rate"),
]


class TestWhenTheSentenceCannotBeSpoken:
    def test_call_60_a_sentence_one_character_over_the_bound_is_too_long_and_nobody_is_called(
        self, joined_agent, speech_origin
    ):
        dialed = speak(joined_agent, "x" * (SPEAK_MAX_CHARACTERS + 1))

        assert (refusal_of(dialed), speech_origin.requests) == ((True, {"refused": "too_long"}), [])

    @pytest.mark.parametrize(("answer", "logged"), SPEECH_FAILS)
    def test_call_60_what_speech_does_not_answer_whole_is_unavailable_with_one_closed_code_logged(
        self, joined_agent, speech_origin, answer, logged
    ):
        speaking(speech_origin, answer)

        with structlog.testing.capture_logs() as logs:
            dialed = speak(joined_agent)
        drained(joined_agent)

        assert (refusal_of(dialed), failures_of(logs)) == ((True, {"refused": "unavailable"}), [logged])
        assert frames_of(joined_agent.member, VOICE) == []

    def test_call_60_an_agent_with_no_held_token_is_unavailable_and_no_workload_token_is_asked_for(
        self, adding_call, joined_agent, speech_origin
    ):
        adding_call.issuer.refuses_token_exchange_grants = True
        for _ in range(3):
            joined_agent.call.pass_time(LEASE_SECONDS - 1)
            joined_agent.standings()
        grants_before = len(adding_call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT))

        with structlog.testing.capture_logs() as logs:
            dialed = speak(joined_agent)

        assert (
            refusal_of(dialed),
            failures_of(logs),
            len(adding_call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT)) - grants_before,
            speech_origin.requests,
        ) == ((True, {"refused": "unavailable"}), [{"outcome": "no_token"}], 0, [])

    def test_call_60_a_workload_token_the_issuer_refuses_is_unavailable_and_speech_is_not_called(
        self, adding_call, joined_agent, speech_origin
    ):
        adding_call.issuer.refuses_client_credentials_grants = True

        with structlog.testing.capture_logs() as logs:
            dialed = speak(joined_agent)

        assert (refusal_of(dialed), [log["outcome"] for log in failures_of(logs)], speech_origin.requests) == (
            (True, {"refused": "unavailable"}),
            ["workload_token_refused"],
            [],
        )


class TestASentenceWithNoText:
    @pytest.mark.parametrize("text", [None, "", "   "], ids=["missing", "empty", "blank"])
    def test_call_60_it_is_unavailable_with_no_text_logged_and_nobody_is_called(
        self, joined_agent, speech_origin, text
    ):
        place = PlaceRef(kind=CALL_KIND, id=joined_agent.owner.room_id).model_dump()
        arguments = {"place": place} if text is None else {"place": place, "text": text}

        with structlog.testing.capture_logs() as logs:
            dialed = joined_agent.call.call(joined_agent.token, SPEAK_TOOL, arguments)

        assert (refusal_of(dialed), failures_of(logs), speech_origin.requests) == (
            (True, {"refused": "unavailable"}),
            [{"outcome": "no_text"}],
            [],
        )


class TestAPersonsOwnAgent:
    def test_call_60_it_has_no_token_held_and_its_speak_is_unavailable_and_nobody_is_called(
        self, joined_agent, speech_origin
    ):
        call = joined_agent.call
        token = call.token(joined_agent.member_account)
        call.call(token, JOIN_CALL, join_arguments(joined_agent.member))
        place = PlaceRef(kind=CALL_KIND, id=joined_agent.member.room_id).model_dump()

        dialed = call.call(token, SPEAK_TOOL, {"place": place, "text": TEXT})

        assert (refusal_of(dialed), speech_origin.requests) == ((True, {"refused": "unavailable"}), [])


class TestAServiceThatIsNotThere:
    @pytest.fixture
    def speaking_service(self):
        gone = Origin()
        origin = gone.base_url
        gone.close()
        return SpeechService(origin, VoiceId.THORSTEN, AUDIENCE_SCOPE)

    def test_call_60_it_is_unavailable_and_logs_unreachable(self, joined_agent):
        with structlog.testing.capture_logs() as logs:
            dialed = speak(joined_agent)

        assert (refusal_of(dialed), failures_of(logs)) == (
            (True, {"refused": "unavailable"}),
            [{"outcome": "unreachable"}],
        )


class TestEveryEndAtTheRoute:
    def test_call_60_a_lease_that_runs_out_drops_the_sound_and_the_next_speak_is_not_active(
        self, joined_agent, speech_origin
    ):
        speaking(speech_origin, chunked_wav(silent_wav(LEASE_SECONDS + 1)))
        speak(joined_agent)
        speak(joined_agent)
        path = f"{VOICE_PATH}/{joined_agent.member.frame_of_type(VOICE)['voice_id']}"

        joined_agent.call.pass_time(LEASE_SECONDS)
        drained(joined_agent)

        assert (
            joined_agent.call.voice(joined_agent.member_login, path).status_code,
            len(frames_of(joined_agent.member, VOICE)),
            refusal_of(speak(joined_agent)),
        ) == (404, 1, (True, {"refused": "not_active"}))

    @pytest.mark.parametrize(
        "arguments",
        [
            pytest.param({"place": {"kind": "elsewhere", "id": "x"}, "text": TEXT}, id="another-kind-of-place"),
            pytest.param({"place": {"kind": CALL_KIND, "id": "no-such-call"}, "text": TEXT}, id="a-call-it-is-not-in"),
            pytest.param({"text": TEXT}, id="no-place"),
        ],
    )
    def test_call_60_a_place_the_agent_holds_no_joined_place_in_is_not_active(self, joined_agent, arguments):
        dialed = joined_agent.call.call(joined_agent.token, SPEAK_TOOL, arguments)

        assert refusal_of(dialed) == (True, {"refused": "not_active"})


class TestNothingLeaks:
    def test_call_60_no_frame_to_a_seat_and_no_route_answer_carries_a_token(self, joined_agent):
        speak(joined_agent)
        answers = []
        for seat in (joined_agent.owner, joined_agent.member):
            fetched = sound_at(joined_agent, seat)
            answers.append(repr(fetched.headers))
        drained(joined_agent)

        issuer = joined_agent.call.issuer
        tokens = [
            *issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT),
            *issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT),
            joined_agent.token,
        ]
        seen = repr([seat.received for seat in (joined_agent.owner, joined_agent.member)]) + repr(answers)
        assert [token for token in tokens if token in seen] == []

    def test_call_60_a_debug_log_of_a_sentence_carries_no_text_no_sound_and_no_token(self, joined_agent, caplog):
        with caplog.at_level(logging.DEBUG), structlog.testing.capture_logs() as logs:
            speak(joined_agent)
            sound_at(joined_agent, joined_agent.member)
        issuer = joined_agent.call.issuer
        tokens = [
            *issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT),
            *issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT),
        ]

        leaked = [needle for needle in (TEXT, SOUND.hex(), *tokens) if needle in caplog.text or needle in repr(logs)]
        assert leaked == []
