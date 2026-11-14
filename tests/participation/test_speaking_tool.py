"""CALL-60: the agent's `speak` at HopIn's real `/mcp` and `/ws`, with a fake speech service on a loopback port, a fake
issuer, and the sound fetched at the route the seats are told."""

import asyncio
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import pytest
import structlog.testing
from agents.member import LEASE_SECONDS, SPEAK_TOOL, STOP_SPEAKING_TOOL, PlaceRef
from speech_client.wire import (
    AUTHORIZATION_HEADER,
    SPEECH_PATH,
    WORKLOAD_TOKEN_HEADER,
    Language,
    RefusalCode,
    VoiceId,
)

from hopin.agent.speaking import SPEAK_MAX_BYTES
from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import CALL_KIND, JOIN_CALL, SPEAK_MAX_CHARACTERS
from hopin.participation.feature import Participation
from hopin.participation.vocabulary import VOICE_PATH, ParticipationSignal
from tests.participation.support.tool_calls import call_in
from tests.support.agent_speak import SENTENCE_SECONDS, TEXT, speak
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
    with_data_size,
    with_sample_rate,
)

pytestmark = pytest.mark.integration

VOICE = ParticipationSignal.AGENT_VOICE.value
LINE = ParticipationSignal.AGENT_LINE.value
SENTENCE_MS = 1500
SOUND = silent_wav(SENTENCE_SECONDS)
STREAMING_DATA_SIZE = 0xFFFFFFFF
CLIENT_CREDENTIALS_GRANT = "client_credentials"
FAILED = "agent_speak_failed"
VOICE_UNAVAILABLE = "agent_voice_unavailable"
UNKNOWN_VOICE = "de_DE-nobody-medium"
UNKNOWN_VOICE_EVENT = "agent_voice_unknown"
ANSWER_SECONDS = 30
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


stop = partial(call_in, tool=STOP_SPEAKING_TOOL)


@pytest.fixture
def speaking_behind(joined_agent):
    """The agent's `speak` call that waits behind its playing sentence, on a worker thread of its own because a call
    answers only when its sentence starts. It returns the call's future once the speaking path has queued the sentence;
    the end of the lease at teardown answers a call still waiting, so no thread stays blocked."""
    pool = ThreadPoolExecutor(max_workers=1)

    def behind():
        stored = joined_agent.call.voices.stored
        waiting = pool.submit(speak, joined_agent)
        joined_agent.call.after_sounds_stored(stored + 1)
        return waiting

    yield behind
    joined_agent.call.pass_time(LEASE_SECONDS)
    pool.shutdown(wait=True)


def still_waiting(scene, call):
    """Whether the call has no answer after the app's loop ran on past everything already due."""
    scene.call.client.portal.call(asyncio.sleep, 0)
    return not call.done()


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

    def test_call_60_a_sentence_behind_the_playing_one_answers_when_the_clock_has_run_out_its_length(
        self, joined_agent, speaking_behind
    ):
        speak(joined_agent)
        second = speaking_behind()
        waiting_at_first = still_waiting(joined_agent, second)

        joined_agent.call.pass_time(SENTENCE_SECONDS - 0.001)
        waiting_just_before = still_waiting(joined_agent, second)
        joined_agent.call.pass_time(0.001)
        answered = second.result(timeout=ANSWER_SECONDS)

        assert (waiting_at_first, waiting_just_before, answered.is_error, answered.outcome) == (
            True,
            True,
            False,
            {"duration_ms": SENTENCE_MS},
        )

    def test_call_60_every_seat_gets_one_voice_and_the_sound_is_served_no_store_to_a_seat_of_the_room(
        self, joined_agent, spoken
    ):
        for seat in (joined_agent.owner, joined_agent.member):
            fetched = sound_at(joined_agent, seat, joined_agent.member_login)

            assert (fetched.status_code, fetched.content, fetched.headers["cache-control"]) == (200, SOUND, "no-store")
        drained(joined_agent)
        assert [len(frames_of(seat, VOICE)) for seat in (joined_agent.owner, joined_agent.member)] == [1, 1]

    def test_call_60_it_stands_once_in_the_chat_and_in_the_agents_window_as_own_and_spoken_once_it_has_ended(
        self, joined_agent, spoken
    ):
        drained(joined_agent)
        playing = joined_agent.entries()

        joined_agent.call.pass_time(SENTENCE_SECONDS)

        [entry] = joined_agent.entries()
        assert (playing, entry["kind"], entry["text"], entry["own"], entry["spoken"]) == (
            [],
            "agent",
            TEXT,
            True,
            True,
        )
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

    def test_call_60_a_sound_that_streams_with_no_size_yet_is_as_long_as_the_frames_it_holds(
        self, joined_agent, speech_origin
    ):
        speaking(speech_origin, chunked_wav(with_data_size(SOUND, STREAMING_DATA_SIZE)))

        dialed = speak(joined_agent)

        assert (dialed.is_error, dialed.outcome) == (False, {"duration_ms": SENTENCE_MS})

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("x" * SPEAK_MAX_CHARACTERS, id="greatest-length"),
            pytest.param("x" * SPEAK_MAX_CHARACTERS + "\n", id="trailing-line-break"),
            pytest.param("x" * 299 + "\n\n   " + "x" * 300, id="run-of-breaks"),
        ],
    )
    def test_call_60_the_bound_counts_the_sentence_after_its_breaks_and_runs_of_spaces_become_one_space(
        self, joined_agent, text
    ):
        dialed = speak(joined_agent, text)

        assert dialed.is_error is False


class TestTheVoiceOfASentence:
    @pytest.fixture
    def accepted_voice(self):
        return VoiceId.KERSTIN

    def test_call_60_each_agent_speaks_with_the_voice_its_accept_named_and_one_that_named_none_with_the_configured_one(
        self, joined_agent, speech_origin
    ):
        joined_agent.add_second_agent()

        speak(joined_agent)
        speak(joined_agent, token=joined_agent.second_token)

        assert [spoken_request(request).voice for request in speech_origin.requests] == [
            VoiceId.KERSTIN,
            VoiceId.THORSTEN,
        ]

    def test_call_60_a_sentence_that_waited_behind_another_takes_the_voice_too(
        self, joined_agent, speaking_behind, speech_origin
    ):
        speak(joined_agent)
        second = speaking_behind()
        joined_agent.call.pass_time(SENTENCE_SECONDS)
        second.result(timeout=ANSWER_SECONDS)

        assert [spoken_request(request).voice for request in speech_origin.requests] == [VoiceId.KERSTIN] * 2


class TestTheLanguageOfASentence:
    @pytest.fixture
    def speaking_service(self, speech_origin):
        return SpeechService(speech_origin.base_url, VoiceId.RYAN, AUDIENCE_SCOPE)

    @pytest.mark.parametrize(
        ("named", "spoken_in"),
        [
            pytest.param({}, Language.DE, id="none-is-german"),
            pytest.param({"language": "en"}, Language.EN, id="en"),
            pytest.param({"language": "de"}, Language.DE, id="de"),
        ],
    )
    def test_call_60_the_sentence_is_spoken_in_the_language_it_names_and_in_german_when_it_names_none(
        self, joined_agent, speech_origin, named, spoken_in
    ):
        dialed = speak(joined_agent, **named)

        [request] = speech_origin.requests
        assert (dialed.is_error, spoken_request(request).language) == (False, spoken_in)

    @pytest.mark.parametrize("language", ["fr", "zz", "DE", "german", ""])
    def test_call_60_a_language_the_voice_or_speech_does_not_know_is_language_unavailable_and_nobody_is_called(
        self, joined_agent, speech_origin, language
    ):
        dialed = speak(joined_agent, language=language)

        assert (refusal_of(dialed), speech_origin.requests) == ((True, {"refused": "language_unavailable"}), [])
        assert joined_agent.call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT) == []

    def test_call_60_a_refused_language_leaves_the_agent_able_to_speak(self, joined_agent, speech_origin):
        speak(joined_agent, language="zz")

        dialed = speak(joined_agent, language="en")

        assert (dialed.is_error, len(speech_origin.requests)) == (False, 1)

    def test_call_60_a_sentence_for_an_inactive_agent_is_not_active_whatever_language_it_names(
        self, joined_agent, speech_origin
    ):
        dialed = speak(joined_agent, room_id="no-such-room", language="zz")

        assert (refusal_of(dialed), speech_origin.requests) == ((True, {"refused": "not_active"}), [])


class TestTheStyleOfASentence:
    @pytest.fixture
    def speaking_service(self, speech_origin):
        return SpeechService(speech_origin.base_url, VoiceId.RYAN, AUDIENCE_SCOPE)

    def test_call_60_the_style_reaches_speech_as_the_instructions_unchanged(self, joined_agent, speech_origin):
        dialed = speak(joined_agent, style="louder and angrier")

        [request] = speech_origin.requests
        assert (dialed.is_error, spoken_request(request).instructions) == (False, "louder and angrier")

    def test_call_60_a_sentence_without_a_style_asks_speech_for_no_instructions(self, joined_agent, speech_origin):
        speak(joined_agent)

        [request] = speech_origin.requests
        assert spoken_request(request).instructions is None

    @pytest.mark.parametrize("style", ["x" * 501, "!!!", "", "   "], ids=["too_long", "no_letter", "empty", "blank"])
    def test_call_60_a_style_speech_refuses_is_style_unavailable_and_the_agent_speaks_on(
        self, joined_agent, speech_origin, style
    ):
        refused = speak(joined_agent, style=style)

        after = speak(joined_agent)
        assert (refusal_of(refused), after.is_error, len(speech_origin.requests)) == (
            (True, {"refused": "style_unavailable"}),
            False,
            1,
        )

    def test_call_60_a_style_the_service_refuses_is_style_unavailable(self, joined_agent, speech_origin):
        speaking(speech_origin, refusal_answer(RefusalCode.INVALID_REQUEST, "instructions"))

        dialed = speak(joined_agent, style="whispering")

        assert refusal_of(dialed) == (True, {"refused": "style_unavailable"})


class TestAVoiceThatTakesNoStyle:
    def test_call_60_a_style_for_a_voice_without_styles_is_style_unavailable_and_speech_is_not_called(
        self, joined_agent, speech_origin
    ):
        dialed = speak(joined_agent, style="louder")

        assert (refusal_of(dialed), speech_origin.requests) == ((True, {"refused": "style_unavailable"}), [])


class TestStoppingASentence:
    def test_call_60_it_cuts_the_playing_sentence_everywhere_and_the_window_holds_its_line_as_cut(self, joined_agent):
        speak(joined_agent)

        stopped = stop(joined_agent)
        drained(joined_agent)

        [entry] = joined_agent.entries()
        assert (
            (stopped.is_error, stopped.outcome),
            [
                len(frames_of(seat, ParticipationSignal.AGENT_STOP.value))
                for seat in (joined_agent.owner, joined_agent.member)
            ],
            (entry["text"], entry["own"], entry["spoken"], entry["cut"]),
        ) == ((False, {"cut": TEXT}), [1, 1], (TEXT, True, True, True))

    def test_call_60_every_sentence_waiting_behind_it_answers_stopped(self, joined_agent, speaking_behind):
        speak(joined_agent)
        waiting = speaking_behind()

        stop(joined_agent)

        answered = waiting.result(timeout=ANSWER_SECONDS)
        assert refusal_of(answered) == (True, {"refused": "stopped"})

    def test_call_60_with_nothing_playing_it_is_nothing_playing(self, joined_agent):
        assert refusal_of(stop(joined_agent)) == (True, {"refused": "nothing_playing"})

    def test_call_60_for_a_place_the_agent_does_not_hold_it_is_not_active(self, joined_agent):
        assert refusal_of(stop(joined_agent, room_id="no-such-room")) == (True, {"refused": "not_active"})

    @pytest.mark.parametrize(
        "arguments",
        [{}, {"place": {"kind": "table", "id": "any"}}],
        ids=["no_place", "a_place_of_another_kind"],
    )
    def test_call_60_a_stop_that_names_no_call_is_not_active(self, joined_agent, arguments):
        dialed = joined_agent.call.call(joined_agent.token, STOP_SPEAKING_TOOL, arguments)

        assert refusal_of(dialed) == (True, {"refused": "not_active"})

    def test_call_60_the_agent_speaks_again_after_a_stop(self, joined_agent):
        speak(joined_agent)
        stop(joined_agent)

        again = speak(joined_agent)

        assert (again.is_error, again.outcome) == (False, {"duration_ms": SENTENCE_MS})


class TestTheLanguageOfAnAgentWithAVoiceOfItsOwn:
    @pytest.fixture
    def accepted_voice(self):
        return VoiceId.NORMAN

    def test_call_60_a_voice_the_accept_named_decides_which_language_it_speaks_not_the_configured_one(
        self, joined_agent, speech_origin
    ):
        german = speak(joined_agent)
        english = speak(joined_agent, language="en")

        assert (refusal_of(german), english.is_error, [spoken_request(r).language for r in speech_origin.requests]) == (
            (True, {"refused": "language_unavailable"}),
            False,
            [Language.EN],
        )


class TestAVoiceSpeechDoesNotName:
    @pytest.fixture
    def accepted_voice(self):
        return UNKNOWN_VOICE

    def test_call_60_the_agent_joins_and_speaks_with_the_configured_voice_and_the_log_holds_a_closed_code_and_no_name(
        self, scene, speech_origin
    ):
        scene.invite()
        with structlog.testing.capture_logs() as logs:
            accepted = scene.accept(scene.standings()[0], voice=UNKNOWN_VOICE)
            scene.owner.frame_of_type(ParticipationSignal.AGENT_JOINED.value)
        spoken = speak(scene)

        unknown = [log for log in logs if log["event"] == UNKNOWN_VOICE_EVENT]
        assert (
            accepted.is_error,
            spoken.is_error,
            [spoken_request(request).voice for request in speech_origin.requests],
            [{key: value for key, value in log.items() if key not in {"event", "log_level"}} for log in unknown],
            UNKNOWN_VOICE in repr(logs),
        ) == (False, False, [VoiceId.THORSTEN], [{"room_id": scene.owner.room_id}], False)


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

        assert (refusal_of(dialed), speech_origin.requests) == ((True, {"refused": "too_long"}), [])

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
            speech_origin.requests,
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

        assert (written.outcome, [log["outcome"] for log in failures_of(logs)], speech_origin.requests) == (
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
        ) == (1, {"duration_ms": SENTENCE_MS}, 1)

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
