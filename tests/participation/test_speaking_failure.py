"""CALL-60, CALL-61: the agent's `speak` when the sentence cannot be spoken or the voice fails."""

import logging
from functools import partial

import pytest
import structlog.testing
from agents.member import LEASE_SECONDS, SPEAK_TOOL, PlaceRef
from speech_client.wire import (
    RefusalCode,
    VoiceId,
)

from hopin.agent.speaking import SPEAK_MAX_BYTES
from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import CALL_KIND, JOIN_CALL, SPEAK_MAX_CHARACTERS
from hopin.participation.vocabulary import VOICE_PATH
from tests.participation.support.speaking_scene import (
    ANSWER_SECONDS,
    CLIENT_CREDENTIALS_GRANT,
    LINE,
    SOUND,
    STREAMING_DATA_SIZE,
    VOICE,
    drained,
    frames_of,
    refusal_of,
    sound_at,
    still_waiting,
)
from tests.support.agent_speak import SENTENCE_SECONDS, TEXT, speak
from tests.support.dialed_call import join_arguments
from tests.support.fake_issuer import TOKEN_EXCHANGE_GRANT
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import (
    AUDIENCE_SCOPE,
    SPOKEN_WAV,
    chunked_wav,
    failures_in,
    not_chunked_answer,
    refusal_answer,
    silent_wav,
    speaking,
    speech_requests,
    with_data_size,
    with_sample_rate,
)

pytestmark = pytest.mark.integration

FAILED = "agent_speak_failed"
VOICE_UNAVAILABLE = "agent_voice_unavailable"
UNREADABLE = "unreadable_audio"
failures_of = partial(failures_in, event=FAILED)


@pytest.fixture
def speaking_service(speech_origin):
    return SpeechService(speech_origin.base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE)


SPEECH_FAILS = [
    pytest.param(refusal_answer(RefusalCode.FORBIDDEN), {"outcome": "refused", "code": "forbidden"}, id="refused"),
    pytest.param(refusal_answer(RefusalCode.BUSY, retry_after_seconds=3), {"outcome": "busy"}, id="busy"),
    pytest.param(chunked_wav(SOUND, terminated=False), {"outcome": "cut"}, id="cut"),
    pytest.param(chunked_wav(SOUND + bytes(SPEAK_MAX_BYTES)), {"outcome": "over_cap"}, id="over-cap"),
    pytest.param(not_chunked_answer(), {"outcome": "malformed"}, id="malformed"),
    pytest.param(chunked_wav(SPOKEN_WAV), {"outcome": "malformed"}, id="unreadable-sound"),
    pytest.param(chunked_wav(with_sample_rate(SOUND, 0)), {"outcome": "malformed"}, id="zero-sample-rate"),
    pytest.param(chunked_wav(SOUND[:-2]), {"outcome": UNREADABLE}, id="fewer-frames-than-declared"),
    pytest.param(
        chunked_wav(with_data_size(SOUND, STREAMING_DATA_SIZE // 2)), {"outcome": UNREADABLE}, id="size-beyond-the-body"
    ),
]


class TestWhenTheSentenceCannotBeSpoken:
    def test_call_60_a_sentence_one_character_over_the_bound_is_too_long_and_nobody_is_called(
        self, joined_agent, speech_origin
    ):
        dialed = speak(joined_agent, "x" * (SPEAK_MAX_CHARACTERS + 1))

        assert (refusal_of(dialed), speech_requests(speech_origin)) == ((True, {"refused": "too_long"}), [])

    def test_call_61_an_agent_with_no_held_token_is_unavailable_and_nothing_is_written(
        self, adding_call, joined_agent, speech_origin
    ):
        adding_call.issuer.refuses_token_exchange_grants = True
        for _ in range(3):
            joined_agent.call.pass_time(LEASE_SECONDS - 1)
            joined_agent.standings()
        grants_before = len(adding_call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT))

        with structlog.testing.capture_logs() as logs:
            dialed = speak(joined_agent)
        drained(joined_agent)

        assert (
            refusal_of(dialed),
            failures_of(logs),
            failures_in(logs, VOICE_UNAVAILABLE),
            len(adding_call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT)) - grants_before,
            speech_requests(speech_origin),
            joined_agent.entries(),
            "voice_state" in joined_agent.window(),
            frames_of(joined_agent.member, LINE),
        ) == ((True, {"refused": "unavailable"}), [{"outcome": "no_token"}], [], 0, [], [], False, [])


class TestWhenTheVoiceFails:
    @pytest.mark.parametrize(("answer", "logged"), SPEECH_FAILS)
    def test_call_61_a_sentence_speech_does_not_answer_whole_is_written_as_the_agents_line_with_one_closed_code_logged(
        self, joined_agent, speech_origin, answer, logged
    ):
        speaking(speech_origin, answer)

        with structlog.testing.capture_logs() as logs:
            written = speak(joined_agent)
        drained(joined_agent)

        [entry] = joined_agent.entries()
        assert (written.is_error, written.outcome, failures_of(logs), failures_in(logs, VOICE_UNAVAILABLE)) == (
            False,
            {"written": True},
            [logged],
            [{"reason": logged["outcome"], "room_id": joined_agent.owner.room_id}],
        )
        assert (entry["text"], entry["own"], entry["spoken"]) == (TEXT, True, False)
        assert [frames_of(seat, VOICE) for seat in (joined_agent.owner, joined_agent.member)] == [[], []]
        assert [len(frames_of(seat, LINE)) for seat in (joined_agent.owner, joined_agent.member)] == [1, 1]

    def test_call_61_a_workload_token_the_issuer_refuses_writes_the_sentence_and_speech_is_not_called(
        self, adding_call, joined_agent, speech_origin
    ):
        adding_call.issuer.refuses_client_credentials_grants = True

        with structlog.testing.capture_logs() as logs:
            written = speak(joined_agent)

        assert (written.outcome, [log["outcome"] for log in failures_of(logs)], speech_requests(speech_origin)) == (
            {"written": True},
            ["workload_token_refused"],
            [],
        )

    def test_call_61_the_window_states_the_voice_unavailable(self, joined_agent, speech_origin):
        speaking(speech_origin, refusal_answer(RefusalCode.BUSY, retry_after_seconds=3))

        speak(joined_agent)

        assert joined_agent.window()["voice_state"] == "unavailable"

    def test_call_61_the_outage_is_logged_once_and_the_voice_is_available_again_at_the_next_sound(
        self, joined_agent, speech_origin
    ):
        speaking(speech_origin, refusal_answer(RefusalCode.BUSY, retry_after_seconds=3))
        with structlog.testing.capture_logs() as logs:
            speak(joined_agent)
            speak(joined_agent)
        speaking(speech_origin, chunked_wav(SOUND))
        recovered = speak(joined_agent)
        joined_agent.call.pass_time(SENTENCE_SECONDS)
        speaking(speech_origin, refusal_answer(RefusalCode.BUSY, retry_after_seconds=3))
        with structlog.testing.capture_logs() as later_logs:
            speak(joined_agent)

        assert (
            len(failures_in(logs, VOICE_UNAVAILABLE)),
            recovered.outcome,
            len(failures_in(later_logs, VOICE_UNAVAILABLE)),
        ) == (1, {"spoken": True}, 1)

    def test_call_61_the_voice_is_stated_available_once_a_sound_follows_the_outage(self, joined_agent, speech_origin):
        speaking(speech_origin, refusal_answer(RefusalCode.BUSY, retry_after_seconds=3))
        speak(joined_agent)
        speaking(speech_origin, chunked_wav(SOUND))

        speak(joined_agent)

        assert joined_agent.window()["voice_state"] == "available"

    def test_call_61_a_voice_that_never_failed_states_nothing(self, joined_agent):
        speak(joined_agent)

        assert "voice_state" not in joined_agent.window()


class TestASentenceWithNoText:
    @pytest.mark.parametrize("text", [None, "", "   "], ids=["missing", "empty", "blank"])
    def test_call_60_it_is_unavailable_with_no_text_logged_and_nobody_is_called(
        self, joined_agent, speech_origin, text
    ):
        place = PlaceRef(kind=CALL_KIND, id=joined_agent.owner.room_id).model_dump()
        arguments = {"place": place} if text is None else {"place": place, "text": text}

        with structlog.testing.capture_logs() as logs:
            dialed = joined_agent.call.call(joined_agent.token, SPEAK_TOOL, arguments)

        assert (refusal_of(dialed), failures_of(logs), speech_requests(speech_origin)) == (
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

        assert (refusal_of(dialed), speech_requests(speech_origin)) == ((True, {"refused": "unavailable"}), [])


class TestAServiceThatIsNotThere:
    @pytest.fixture
    def speaking_service(self):
        gone = Origin()
        origin = gone.base_url
        gone.close()
        return SpeechService(origin, VoiceId.THORSTEN, AUDIENCE_SCOPE)

    def test_call_61_the_sentence_is_written_and_unreachable_is_logged(self, joined_agent):
        with structlog.testing.capture_logs() as logs:
            written = speak(joined_agent)

        assert (written.outcome, failures_of(logs)) == ({"written": True}, [{"outcome": "unreachable"}])


class TestEveryEndAtTheRoute:
    def test_call_60_a_lease_that_runs_out_drops_the_sound_and_the_next_speak_is_not_active(
        self, joined_agent, speech_origin, speaking_behind
    ):
        speaking(speech_origin, chunked_wav(silent_wav(LEASE_SECONDS + 1)))
        speak(joined_agent)
        waiting = speaking_behind()
        waiting_before = still_waiting(joined_agent, waiting)
        path = f"{VOICE_PATH}/{joined_agent.member.frame_of_type(VOICE)['voice_id']}"

        joined_agent.call.pass_time(LEASE_SECONDS)
        drained(joined_agent)

        assert (
            waiting_before,
            joined_agent.call.voice(joined_agent.member_login, path).status_code,
            len(frames_of(joined_agent.member, VOICE)),
            refusal_of(waiting.result(timeout=ANSWER_SECONDS)),
            refusal_of(speak(joined_agent)),
        ) == (True, 404, 1, (True, {"refused": "not_active"}), (True, {"refused": "not_active"}))


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
