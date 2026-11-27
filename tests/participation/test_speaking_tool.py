"""The agent's `speak` at HopIn's real `/mcp` and `/ws`, with a fake speech service on a loopback port, a fake
issuer, and the sound fetched at the route the seats are told."""

import asyncio
import secrets
from functools import partial

import pytest
from agents.member import STOP_SPEAKING_TOOL
from logline import Outcome
from speech_client.wire import (
    AUTHORIZATION_HEADER,
    HEALTH_PATH,
    SPEECH_PATH,
    WORKLOAD_TOKEN_HEADER,
    Language,
    RefusalCode,
    VoiceId,
)

from hopin.agent import speaking as speaking_module
from hopin.agent.speaking import HEALTH_MAX_BYTES
from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import SPEAK_MAX_CHARACTERS
from hopin.participation.feature import Participation
from hopin.participation.vocabulary import VOICE_PATH, VOICE_RECOVERY_SECONDS, ParticipationSignal
from hopin.rooms.reference import room_reference
from tests.participation.support.speaking_scene import (
    ANSWER_SECONDS,
    CLIENT_CREDENTIALS_GRANT,
    LINE,
    SOUND,
    STREAMING_DATA_SIZE,
    VOICE,
    drained,
    frames_of,
    grants,
    refusal_of,
    sound_at,
    still_waiting,
)
from tests.participation.support.tool_calls import call_in
from tests.support.agent_speak import SENTENCE_SECONDS, TEXT, speak
from tests.support.fake_issuer import TOKEN_EXCHANGE_GRANT, TOKEN_LIFETIME_SECONDS
from tests.support.log_lines import captured_lines
from tests.support.loopback_origin import http_response
from tests.support.speech_origin import (
    AUDIENCE_SCOPE,
    chunked_wav,
    header_of,
    offering,
    refusal_answer,
    speak_failures,
    speaking,
    speech_requests,
    spoken_request,
    with_data_size,
)

pytestmark = pytest.mark.integration

SENTENCE_MS = 1500
HEALTH_DEADLINE_FOR_A_STALL_SECONDS = 0.2
UNKNOWN_VOICE = "de_DE-nobody-medium"
UNKNOWN_VOICE_EVENT = "voice_list.refused"
failures_of = speak_failures


@pytest.fixture
def speaking_service(speech_origin):
    return SpeechService(speech_origin.base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE)


stop = partial(call_in, tool=STOP_SPEAKING_TOOL)


class TestSpeakingOff:
    @pytest.fixture
    def speaking_service(self):
        return None

    def test_call_60_with_the_setting_off_speak_is_unavailable_logs_speaking_off_and_nobody_is_called(
        self, joined_agent
    ):
        """CALL-60."""
        with captured_lines() as logs:
            dialed = speak(joined_agent)
        drained(joined_agent)

        issuer = joined_agent.call.issuer
        assert refusal_of(dialed) == (True, {"refused": "unavailable"})
        assert failures_of(logs) == ["speaking_off"]
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
        """CALL-60."""
        assert (spoken.is_error, spoken.outcome) == (False, {"spoken": True})

    def test_call_60_a_sentence_behind_the_playing_one_answers_when_the_clock_has_run_out_its_length(
        self, joined_agent, speaking_behind
    ):
        """CALL-60."""
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
            {"spoken": True},
        )

    def test_call_60_every_seat_gets_one_voice_and_the_sound_is_served_no_store_to_a_seat_of_the_room(
        self, joined_agent, spoken
    ):
        """CALL-60."""
        for seat in (joined_agent.owner, joined_agent.member):
            fetched = sound_at(joined_agent, seat, joined_agent.member_login)

            assert (fetched.status_code, fetched.content, fetched.headers["cache-control"]) == (200, SOUND, "no-store")
        drained(joined_agent)
        assert [len(frames_of(seat, VOICE)) for seat in (joined_agent.owner, joined_agent.member)] == [1, 1]

    def test_call_60_it_stands_once_in_the_chat_and_in_the_agents_window_as_own_and_spoken_once_it_has_ended(
        self, joined_agent, spoken
    ):
        """CALL-60."""
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

    def test_call_60_the_window_read_marks_the_line_whose_next_sentence_already_waits_as_continues(
        self, joined_agent, spoken, speaking_behind
    ):
        """CALL-60."""
        second = speaking_behind()

        joined_agent.call.pass_time(SENTENCE_SECONDS)
        second.result(timeout=ANSWER_SECONDS)
        joined_agent.call.pass_time(SENTENCE_SECONDS)

        assert [entry.get("continues", False) for entry in joined_agent.entries()] == [True, False]

    def test_call_60_the_sentence_goes_to_speech_on_the_agents_held_token_and_hopins_workload_token(
        self, joined_agent, spoken, speech_origin
    ):
        """CALL-60."""
        issuer = joined_agent.call.issuer

        [request] = speech_requests(speech_origin)
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
        """CALL-60."""
        speaking(speech_origin, chunked_wav(with_data_size(SOUND, STREAMING_DATA_SIZE)))

        dialed = speak(joined_agent)

        assert (dialed.is_error, dialed.outcome) == (False, {"spoken": True})

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
        """CALL-60."""
        dialed = speak(joined_agent, text)

        assert dialed.is_error is False


def speak_until_it_ends(scene):
    """A sentence that is spoken to its end, so that the next one does not wait behind it."""
    spoken = speak(scene)
    scene.call.pass_time(SENTENCE_SECONDS)
    return spoken


class TestTheWorkloadToken:
    def test_call_60_sentences_within_one_tokens_lifetime_post_one_grant_and_carry_the_same_token(
        self, joined_agent, speech_origin
    ):
        """CALL-60: HopIn's token for the service is fetched once for the sentences that follow within its lifetime."""
        for _ in range(2):
            speak_until_it_ends(joined_agent)

        assert (
            len(grants(joined_agent, CLIENT_CREDENTIALS_GRANT)),
            {header_of(request, WORKLOAD_TOKEN_HEADER) for request in speech_requests(speech_origin)},
        ) == (1, set(joined_agent.call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT)))

    def test_call_60_a_token_at_the_end_of_its_lifetime_is_fetched_anew_before_the_next_sentence(
        self, joined_agent, issuer_clock
    ):
        """CALL-60."""
        speak_until_it_ends(joined_agent)
        issuer_clock.advance(TOKEN_LIFETIME_SECONDS)
        speak_until_it_ends(joined_agent)

        assert len(grants(joined_agent, CLIENT_CREDENTIALS_GRANT)) == 2

    def test_call_61_a_refusal_is_not_reused_for_the_next_sentence(self, joined_agent):
        """CALL-61."""
        issuer = joined_agent.call.issuer
        issuer.refuses_client_credentials_grants = True
        refused = speak(joined_agent)
        issuer.refuses_client_credentials_grants = False

        spoken = speak(joined_agent)

        assert (refused.outcome, spoken.outcome, len(grants(joined_agent, CLIENT_CREDENTIALS_GRANT))) == (
            {"written": True},
            {"spoken": True},
            2,
        )


class TestTheVoiceOfASentence:
    @pytest.fixture
    def accepted_voices(self):
        return (VoiceId.KERSTIN,)

    def test_call_60_each_agent_speaks_with_the_voice_its_accept_named_and_one_that_named_none_with_the_configured_one(
        self, joined_agent, speech_origin
    ):
        """CALL-60."""
        joined_agent.add_second_agent()

        speak(joined_agent)
        speak(joined_agent, token=joined_agent.second_token)

        assert [spoken_request(request).voice for request in speech_requests(speech_origin)] == [
            VoiceId.KERSTIN,
            VoiceId.THORSTEN,
        ]

    def test_call_60_a_sentence_that_waited_behind_another_takes_the_voice_too(
        self, joined_agent, speaking_behind, speech_origin
    ):
        """CALL-60."""
        speak(joined_agent)
        second = speaking_behind()
        joined_agent.call.pass_time(SENTENCE_SECONDS)
        second.result(timeout=ANSWER_SECONDS)

        assert [spoken_request(request).voice for request in speech_requests(speech_origin)] == [VoiceId.KERSTIN] * 2


def spoken_voices(origin):
    return [spoken_request(request).voice for request in speech_requests(origin)]


class TestTheVoicesOfAnAgent:
    @pytest.fixture
    def accepted_voices(self):
        return (VoiceId.RYAN, VoiceId.THORSTEN)

    def test_call_60_the_sentence_is_spoken_with_the_first_voice_of_the_list_that_speech_offers(
        self, joined_agent, speech_origin
    ):
        """CALL-60."""
        speak(joined_agent)

        assert spoken_voices(speech_origin) == [VoiceId.RYAN]

    def test_call_60_the_next_voice_of_the_list_speaks_once_the_first_is_not_offered_and_no_restart_is_needed(
        self, joined_agent, speech_origin
    ):
        """CALL-60."""
        first = speak(joined_agent)
        joined_agent.call.pass_time(SENTENCE_SECONDS)
        offering(speech_origin, VoiceId.THORSTEN)

        second = speak(joined_agent)

        assert ((first.is_error, second.is_error), spoken_voices(speech_origin)) == (
            (False, False),
            [VoiceId.RYAN, VoiceId.THORSTEN],
        )

    def test_call_60_a_voice_that_is_offered_but_not_ready_is_passed_over(self, joined_agent, speech_origin):
        """CALL-60."""
        offering(speech_origin, VoiceId.THORSTEN, unready=(VoiceId.RYAN,))

        speak(joined_agent)

        assert spoken_voices(speech_origin) == [VoiceId.THORSTEN]

    def test_call_60_a_voice_of_the_list_that_takes_no_style_speaks_the_sentence_without_it(
        self, joined_agent, speech_origin
    ):
        """CALL-60."""
        offering(speech_origin, VoiceId.THORSTEN)

        dialed = speak(joined_agent, style="louder")

        [request] = speech_requests(speech_origin)
        assert (dialed.is_error, spoken_request(request).instructions) == (False, None)

    def test_call_61_a_list_with_no_voice_offered_is_written_and_the_window_states_the_voice_unavailable(
        self, joined_agent, speech_origin
    ):
        """CALL-61."""
        offering(speech_origin, VoiceId.KERSTIN)

        dialed = speak(joined_agent)

        assert (dialed.outcome, spoken_voices(speech_origin), joined_agent.window()["voice_state"]) == (
            {"written": True},
            [],
            "unavailable",
        )

    @pytest.mark.parametrize(
        ("answer", "reason"),
        [
            pytest.param(http_response("500 Internal Server Error", b""), "unexpected_status", id="unexpected-status"),
            pytest.param(http_response("200 OK", b"{"), "malformed", id="malformed"),
            pytest.param(http_response("200 OK", bytes(HEALTH_MAX_BYTES + 1)), "over_cap", id="over-cap"),
            pytest.param(http_response("200 OK", b"{", declared_length=HEALTH_MAX_BYTES), "cut", id="cut"),
            pytest.param(None, "past_deadline", id="past-deadline"),
            pytest.param(b"", "unreachable", id="unreachable"),
        ],
    )
    def test_call_61_a_health_that_cannot_be_read_ends_the_health_step_refused_with_its_cause(
        self, joined_agent, speech_origin, monkeypatch, answer, reason
    ):
        """CALL-61."""
        offering(speech_origin, VoiceId.KERSTIN)
        speak(joined_agent)
        if answer is None:
            monkeypatch.setattr(speaking_module, "HEALTH_DEADLINE_SECONDS", HEALTH_DEADLINE_FOR_A_STALL_SECONDS)
            speech_origin.serve(HEALTH_PATH, b"", held=True)
        elif answer:
            speech_origin.serve(HEALTH_PATH, answer)
        else:
            speech_origin.close()

        with captured_lines() as logs:
            joined_agent.call.pass_time(VOICE_RECOVERY_SECONDS)
            assert logs.wait_for_event("speech_health.ended", 5)

        [health] = [log for log in logs if log["event"] == "speech_health.ended"]
        assert (health["outcome"], health["reason"]) == (Outcome.REFUSED, reason)

    def test_call_61_the_window_turns_available_again_without_a_sentence_once_a_voice_of_the_list_is_offered(
        self, joined_agent, speech_origin
    ):
        """CALL-61."""
        offering(speech_origin, VoiceId.KERSTIN)
        speak(joined_agent)
        joined_agent.call.pass_time(VOICE_RECOVERY_SECONDS)
        still = joined_agent.window()["voice_state"]
        offering(speech_origin, VoiceId.KERSTIN, VoiceId.THORSTEN)

        joined_agent.call.pass_time(VOICE_RECOVERY_SECONDS)

        assert (still, joined_agent.window()["voice_state"], spoken_voices(speech_origin)) == (
            "unavailable",
            "available",
            [],
        )

    def test_call_60_an_agent_whose_accept_named_no_voice_speaks_with_the_configured_voice_whatever_the_others_name(
        self, joined_agent, speech_origin
    ):
        """CALL-60."""
        joined_agent.add_second_agent()

        speak(joined_agent, token=joined_agent.second_token)

        assert spoken_voices(speech_origin) == [VoiceId.THORSTEN]

    def test_call_60_an_agent_whose_accept_names_a_voice_speech_does_not_know_first_speaks_with_the_next_one(
        self, scene, speech_origin
    ):
        """CALL-60."""
        scene.invite()
        scene.accept(scene.standings()[0], voices=[UNKNOWN_VOICE, VoiceId.KERSTIN])
        scene.owner.frame_of_type(ParticipationSignal.AGENT_JOINED.value)

        speak(scene)

        assert spoken_voices(speech_origin) == [VoiceId.KERSTIN]


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
        """CALL-60."""
        dialed = speak(joined_agent, **named)

        [request] = speech_requests(speech_origin)
        assert (dialed.is_error, spoken_request(request).language) == (False, spoken_in)

    @pytest.mark.parametrize("language", ["fr", "zz"])
    def test_call_60_a_language_the_voice_or_speech_does_not_know_is_language_unavailable_and_nobody_is_called(
        self, joined_agent, speech_origin, language
    ):
        """CALL-60."""
        dialed = speak(joined_agent, language=language)

        assert (refusal_of(dialed), speech_requests(speech_origin)) == ((True, {"refused": "language_unavailable"}), [])
        assert joined_agent.call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT) == []

    def test_call_60_a_refused_language_leaves_the_agent_able_to_speak(self, joined_agent, speech_origin):
        """CALL-60."""
        speak(joined_agent, language="zz")

        dialed = speak(joined_agent, language="en")

        assert (dialed.is_error, len(speech_requests(speech_origin))) == (False, 1)

    def test_call_60_a_sentence_for_an_inactive_agent_is_not_active_whatever_language_it_names(
        self, joined_agent, speech_origin
    ):
        """CALL-60."""
        dialed = speak(joined_agent, room_id="no-such-room", language="zz")

        assert (refusal_of(dialed), speech_requests(speech_origin)) == ((True, {"refused": "not_active"}), [])


class TestTheStyleOfASentence:
    @pytest.fixture
    def speaking_service(self, speech_origin):
        return SpeechService(speech_origin.base_url, VoiceId.RYAN, AUDIENCE_SCOPE)

    def test_call_60_the_style_reaches_speech_as_the_instructions_unchanged(self, joined_agent, speech_origin):
        """CALL-60."""
        dialed = speak(joined_agent, style="louder and angrier")

        [request] = speech_requests(speech_origin)
        assert (dialed.is_error, spoken_request(request).instructions) == (False, "louder and angrier")

    def test_call_60_a_sentence_without_a_style_asks_speech_for_no_instructions(self, joined_agent, speech_origin):
        """CALL-60."""
        speak(joined_agent)

        [request] = speech_requests(speech_origin)
        assert spoken_request(request).instructions is None

    @pytest.mark.parametrize("style", ["x" * 501, "!!!", "   "], ids=["too_long", "no_letter", "blank"])
    def test_call_60_a_style_speech_refuses_is_style_unavailable_and_the_agent_speaks_on(
        self, joined_agent, speech_origin, style
    ):
        """CALL-60."""
        refused = speak(joined_agent, style=style)

        after = speak(joined_agent)
        assert (refusal_of(refused), after.is_error, len(speech_requests(speech_origin))) == (
            (True, {"refused": "style_unavailable"}),
            False,
            1,
        )

    def test_call_60_a_style_the_service_refuses_is_style_unavailable(self, joined_agent, speech_origin):
        """CALL-60."""
        speaking(speech_origin, refusal_answer(RefusalCode.INVALID_REQUEST, "instructions"))

        dialed = speak(joined_agent, style="whispering")

        assert refusal_of(dialed) == (True, {"refused": "style_unavailable"})


@pytest.mark.parametrize(
    ("named", "told"),
    [
        pytest.param({"mood": "happy"}, {"mood": "happy"}, id="with a mood"),
        pytest.param({}, {}, id="without a mood"),
    ],
)
def test_call_67_every_seat_is_told_the_voice_with_the_mood_of_the_sentence_and_no_mood_it_does_not_name(
    joined_agent, named, told
):
    """CALL-67."""
    speak(joined_agent, **named)
    drained(joined_agent)

    for seat in (joined_agent.owner, joined_agent.member):
        [frame] = frames_of(seat, VOICE)
        assert {key: frame[key] for key in frame.keys() & {"mood"}} == told


class TestAVoiceThatTakesNoStyle:
    def test_call_60_a_style_for_a_voice_without_styles_is_style_unavailable_and_speech_is_not_called(
        self, joined_agent, speech_origin
    ):
        """CALL-60."""
        dialed = speak(joined_agent, style="louder")

        assert (refusal_of(dialed), speech_requests(speech_origin)) == ((True, {"refused": "style_unavailable"}), [])


class TestStoppingASentence:
    def test_call_60_it_cuts_the_playing_sentence_everywhere_and_the_window_holds_its_line_as_cut(self, joined_agent):
        """CALL-60."""
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
        """CALL-60."""
        speak(joined_agent)
        waiting = speaking_behind()

        stop(joined_agent)

        answered = waiting.result(timeout=ANSWER_SECONDS)
        assert refusal_of(answered) == (True, {"refused": "stopped"})

    def test_call_60_with_nothing_playing_it_is_nothing_playing(self, joined_agent):
        """CALL-60."""
        assert refusal_of(stop(joined_agent)) == (True, {"refused": "nothing_playing"})

    def test_call_60_for_a_place_the_agent_does_not_hold_it_is_not_active(self, joined_agent):
        """CALL-60."""
        assert refusal_of(stop(joined_agent, room_id="no-such-room")) == (True, {"refused": "not_active"})

    def test_call_60_a_stop_at_a_place_of_another_kind_is_not_active(self, joined_agent):
        """CALL-60."""
        dialed = joined_agent.call.call(
            joined_agent.token, STOP_SPEAKING_TOOL, {"place": {"kind": "table", "id": "any"}}
        )

        assert refusal_of(dialed) == (True, {"refused": "not_active"})

    def test_call_60_the_agent_speaks_again_after_a_stop(self, joined_agent):
        """CALL-60."""
        speak(joined_agent)
        stop(joined_agent)

        again = speak(joined_agent)

        assert (again.is_error, again.outcome) == (False, {"spoken": True})


class TestTheLanguageOfAnAgentWithAVoiceOfItsOwn:
    @pytest.fixture
    def accepted_voices(self):
        return (VoiceId.NORMAN,)

    def test_call_60_a_voice_the_accept_named_decides_which_language_it_speaks_not_the_configured_one(
        self, joined_agent, speech_origin
    ):
        """CALL-60."""
        german = speak(joined_agent)
        english = speak(joined_agent, language="en")

        assert (
            refusal_of(german),
            english.is_error,
            [spoken_request(r).language for r in speech_requests(speech_origin)],
        ) == (
            (True, {"refused": "language_unavailable"}),
            False,
            [Language.EN],
        )


class TestAVoiceSpeechDoesNotName:
    @pytest.fixture
    def accepted_voices(self):
        return (UNKNOWN_VOICE,)

    def test_call_60_the_agent_joins_and_speaks_with_the_configured_voice_and_the_log_holds_a_closed_code_and_no_name(
        self, scene, speech_origin
    ):
        """CALL-60."""
        scene.invite()
        with captured_lines() as logs:
            accepted = scene.accept(scene.standings()[0], voices=[UNKNOWN_VOICE])
            scene.owner.frame_of_type(ParticipationSignal.AGENT_JOINED.value)
        spoken = speak(scene)

        unknown = [log for log in logs if log["event"] == UNKNOWN_VOICE_EVENT]
        assert (
            accepted.is_error,
            spoken.is_error,
            [spoken_request(request).voice for request in speech_requests(speech_origin)],
            [{key: value for key, value in log.items() if key not in {"event", "log_level"}} for log in unknown],
            UNKNOWN_VOICE in repr(logs),
        ) == (
            False,
            False,
            [VoiceId.THORSTEN],
            [{"reason": "unknown_voice", "room_ref": room_reference(scene.owner.room_id)}],
            False,
        )


class TestTheVoiceRoute:
    @pytest.fixture
    def path(self, joined_agent):
        speak(joined_agent)
        return f"{VOICE_PATH}/{joined_agent.member.frame_of_type(VOICE)['voice_id']}"

    def test_call_60_a_signed_in_person_with_no_seat_in_the_room_gets_nothing(self, joined_agent, path):
        """CALL-60."""
        [_, stranger] = joined_agent.call.member()

        assert joined_agent.call.voice(stranger, path).status_code == 404

    def test_call_60_without_a_session_the_gate_answers_before_the_route(self, joined_agent, path):
        """CALL-60."""
        assert joined_agent.call.client.get(path, headers={"accept": "*/*"}).status_code == 401

    def test_call_60_the_seat_check_runs_on_the_event_loop_which_changes_the_seats(
        self, joined_agent, path, monkeypatch
    ):
        """CALL-60."""
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
        """CALL-60."""
        unknown = f"{VOICE_PATH}/{secrets.token_urlsafe(16)}"

        assert joined_agent.call.voice(joined_agent.member_login, unknown).status_code == 404
