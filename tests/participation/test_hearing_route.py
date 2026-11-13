"""CALL-58: the hearing route at HopIn's real `/ws` composition, with a fake speech service on a loopback port, a fake
issuer, and the agent's window read through the real `/mcp`."""

import asyncio
import base64
import json
import logging
import threading
from http import HTTPStatus

import pytest
import structlog.testing
from agents.member import LEASE_SECONDS
from speech_client.hearing import HearingSession
from speech_client.relay import HopClose
from speech_client.wire import (
    AUTHORIZATION_HEADER,
    WORKLOAD_TOKEN_HEADER,
    Language,
    RefusalCode,
    SessionUpdate,
    VoiceId,
)
from starlette.websockets import WebSocketDisconnect

from hopin.agent import hearing_route
from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import JOIN_CALL, LEAVE_CALL
from hopin.config.constants import POLICY_VIOLATION_CLOSE_CODE, SESSION_RECHECK_SECONDS, SignalType
from hopin.participation.feature import Participation
from hopin.participation.vocabulary import HEARING_PATH, HearingClose, HearingRefusal, ParticipationSignal
from tests.support.dialed_call import join_arguments
from tests.support.fake_issuer import TOKEN_EXCHANGE_GRANT
from tests.support.fake_speech import WAIT_SECONDS, FakeSpeech, Refusal, a_sentence, completed, failing, refused_with
from tests.support.signed_in import handshake_headers

pytestmark = pytest.mark.integration

AUDIENCE_SCOPE = "speech-audience"
OFFERS = ParticipationSignal.AGENT_OFFERS.value
SOUND = bytes(100)
SLOW_APP_SECONDS = 0.3
CLIENT_CREDENTIALS_GRANT = "client_credentials"


@pytest.fixture
def fake_speech():
    service = FakeSpeech()
    service.start()
    yield service
    service.stop()


@pytest.fixture
def hearing_service(fake_speech):
    return SpeechService(fake_speech.origin, VoiceId.THORSTEN, AUDIENCE_SCOPE)


@pytest.fixture
def sponsored(joined_agent):
    """The scene with its owner's agent joined, so the agent hears the owner; the sponsor's switch is told on."""
    joined_agent.owner.frame_of_type(ParticipationSignal.AGENT_HEARING.value)
    return joined_agent


def hop(scene, *, seat=None, login=...):
    """The hearing socket of a seat, opened as that seat's browser opens it; the owner's seat and login by default, and
    no login at all for `None`."""
    seat = scene.owner if seat is None else seat
    return scene.call.client.websocket_connect(
        f"{HEARING_PATH}/{seat.participant_id}", headers=handshake_headers(scene.owner_login if login is ... else login)
    )


def closed_with(socket):
    message = socket.receive()
    assert message["type"] == "websocket.close"
    return message["code"]


def entries_of(scene, token=None, room_id=None):
    answer = scene.call.window(
        scene.token if token is None else token, scene.owner.room_id if room_id is None else room_id
    )
    return json.loads(answer["result"]["contents"][0]["text"])["entries"]


def header_of(session, name):
    return next(value for header, value in session.headers.items() if header.lower() == name.lower())


def switch_off(scene):
    scene.owner.send({"type": ParticipationSignal.AGENT_HEARING.value, "agent_id": scene.agent_id, "on": False})


def lose_the_held_token(scene):
    """The agent keeps calling within its lease, the issuer refuses each new exchange, and the token it held lapses."""
    scene.call.issuer.refuses_token_exchange_grants = True
    for _ in range(3):
        scene.call.pass_time(LEASE_SECONDS - 1)
        scene.standings()


def grants(scene, grant_type):
    return [request for request, _ in scene.call.issuer.exchanges if request.form["grant_type"] == grant_type]


def hear_one_sentence(scene, fake_speech, text="Guten Tag", **sentence):
    """A hop in which the service hears one sentence and ends in order; the code the browser's socket closed with."""
    with hop(scene) as socket:
        socket.send_bytes(SOUND)
        session = fake_speech.next_session()
        for event in a_sentence(text, **sentence):
            session.say(event)
        session.hang_up()
        return closed_with(socket)


class TestAFinishedSentence:
    def test_call_58_it_enters_the_window_of_the_agent_that_hears_as_a_spoken_line_of_kind_person_under_the_shown_name(
        self, sponsored, fake_speech
    ):
        """CALL-58: With everything on, a finished sentence is one `PlaceEntry` of kind person, with the speaker's
        shown name and `spoken` true."""
        closed = hear_one_sentence(sponsored, fake_speech)

        [entry] = entries_of(sponsored)
        assert (closed, entry) == (
            HearingClose.NORMAL,
            {
                "seq": entry["seq"],
                "kind": "person",
                "name": sponsored.owner_account.username,
                "text": "Guten Tag",
                "own": False,
                "spoken": True,
            },
        )

    def test_call_58_a_sentence_the_service_says_before_the_app_sent_its_update_still_enters_the_window(
        self, sponsored, fake_speech, monkeypatch
    ):
        """CALL-58: The service says nothing before the app's session update, so a slow app loses no sentence."""
        connect = HearingSession._connect  # noqa: SLF001 - the app's slowness is the arrangement

        async def slow_connect(session):
            socket = await connect(session)
            await asyncio.sleep(SLOW_APP_SECONDS)
            return socket

        monkeypatch.setattr(HearingSession, "_connect", slow_connect)

        closed = hear_one_sentence(sponsored, fake_speech)

        assert (closed, [entry["text"] for entry in entries_of(sponsored)]) == (HearingClose.NORMAL, ["Guten Tag"])

    def test_call_58_it_is_asked_for_german_and_english(self, sponsored, fake_speech):
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.wait_for_audio(1)
            session.hang_up()
            closed_with(socket)

        update = SessionUpdate.model_validate_json(session.client_messages[0])
        assert update.session.audio.input.transcription.allowed == (Language.DE, Language.EN)

    def test_call_58_the_service_gets_both_tokens_and_the_browser_only_a_close_code(self, sponsored, fake_speech):
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.hang_up()
            closed = closed_with(socket)

        [workload] = sponsored.call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT)
        [person] = sponsored.call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)
        assert (closed, header_of(session, WORKLOAD_TOKEN_HEADER), header_of(session, AUTHORIZATION_HEADER)) == (
            HearingClose.NORMAL,
            workload,
            f"Bearer {person}",
        )

    @pytest.mark.parametrize(
        "events",
        [
            pytest.param(lambda: [completed("")], id="an_empty_transcript"),
            pytest.param(lambda: [completed("   ")], id="a_blank_transcript"),
            pytest.param(lambda: list(a_sentence("Guten", started_ms=None, stopped_ms=None))[:-1], id="deltas_only"),
        ],
    )
    def test_call_58_an_empty_transcript_and_deltas_enter_no_window(self, sponsored, fake_speech, events):
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            for event in events():
                session.say(event)
            session.hang_up()
            closed_with(socket)

        assert entries_of(sponsored) == []

    def test_call_58_a_sentence_began_at_the_moment_the_hop_opened_plus_its_position_in_the_sound(
        self, sponsored, fake_speech, monkeypatch
    ):
        """CALL-58: The record reads the scheduler's clock, so the start is that clock at the hop's opening plus the
        service's position; a sentence without a position begins where the one before it ended."""
        began = []
        heard = Participation.heard

        def recording(participation, seat, text, began_at):
            began.append((text, began_at))
            heard(participation, seat, text, began_at)

        monkeypatch.setattr(Participation, "heard", recording)
        sponsored.call.pass_time(40)
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            for event in (
                *a_sentence("Erster", started_ms=500, stopped_ms=900),
                *a_sentence("Zweiter", started_ms=None, stopped_ms=None),
            ):
                session.say(event)
            session.hang_up()
            closed_with(socket)

        assert began == [("Erster", pytest.approx(40.5)), ("Zweiter", pytest.approx(40.9))]

    def test_call_58_two_sentences_enter_in_the_order_they_were_heard(self, sponsored, fake_speech):
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            for event in (*a_sentence("Erster"), *a_sentence("Zweiter", started_ms=None, stopped_ms=None)):
                session.say(event)
            session.hang_up()
            closed_with(socket)

        assert [entry["text"] for entry in entries_of(sponsored)] == ["Erster", "Zweiter"]


class TestOnlyTheSponsoredAgentHears:
    def test_call_58_another_agent_in_the_call_hears_nothing_of_the_sponsor(self, sponsored, fake_speech):
        """CALL-58: The sentence reaches the agent whose switch for the seat is on and no other agent of the call."""
        call = sponsored.call
        other_token = call.token(sponsored.member_account)
        call.call(other_token, JOIN_CALL, join_arguments(sponsored.member))

        hear_one_sentence(sponsored, fake_speech)

        assert (len(entries_of(sponsored)), entries_of(sponsored, other_token, sponsored.member.room_id)) == (1, [])


class TestARefusedHop:
    def test_call_58_a_seat_no_agent_hears_gets_a_code_and_no_service_session_and_no_token_exchange(
        self, sponsored, fake_speech
    ):
        """CALL-58: The member's seat has no agent that hears it."""
        exchanges_before = len(sponsored.call.issuer.exchanges)

        with hop(sponsored, seat=sponsored.member, login=sponsored.member_login) as socket:
            closed = closed_with(socket)

        assert (closed, fake_speech.sessions, len(sponsored.call.issuer.exchanges)) == (
            HearingClose.NOBODY_HEARS,
            [],
            exchanges_before,
        )

    def test_call_58_another_accounts_seat_gets_a_code_and_no_service_session(self, sponsored, fake_speech):
        """CALL-58: A seat an account does not hold is refused, though an agent hears it."""
        with hop(sponsored, login=sponsored.member_login) as socket:
            closed = closed_with(socket)

        assert (closed, fake_speech.sessions) == (HearingClose.NO_SEAT, [])

    def test_call_58_a_seat_that_waits_to_resume_gets_a_code(self, sponsored, fake_speech, seat_changes):
        seat_changes.clear()
        sponsored.owner.drop()
        assert seat_changes.wait(timeout=10)

        with hop(sponsored) as socket:
            closed = closed_with(socket)

        assert (closed, fake_speech.sessions) == (HearingClose.SEAT_AWAY, [])

    def test_call_58_an_agent_without_a_held_token_gets_a_code_and_no_workload_token_is_asked_for(
        self, sponsored, fake_speech
    ):
        """CALL-58: With no token held for the agent, nothing is exchanged and nothing reaches the service."""
        lose_the_held_token(sponsored)
        workload_grants = len(grants(sponsored, CLIENT_CREDENTIALS_GRANT))

        with hop(sponsored) as socket:
            closed = closed_with(socket)

        assert (closed, len(grants(sponsored, CLIENT_CREDENTIALS_GRANT)) - workload_grants, fake_speech.sessions) == (
            HearingClose.NO_TOKEN,
            0,
            [],
        )

    def test_call_58_a_refused_workload_token_gets_a_service_code_and_no_service_session(self, sponsored, fake_speech):
        sponsored.call.issuer.refuses_client_credentials_grants = True

        with hop(sponsored) as socket:
            closed = closed_with(socket)

        assert (closed, fake_speech.sessions) == (HearingClose.SERVICE_FAILED, [])

    def test_call_58_a_socket_without_a_session_is_refused_before_it_is_accepted(self, sponsored, fake_speech):
        with pytest.raises(WebSocketDisconnect) as refused, hop(sponsored, login=None):
            pass

        assert (refused.value.code, fake_speech.sessions) == (POLICY_VIOLATION_CLOSE_CODE, [])


class TestTheCloseCodes:
    def test_call_58_every_refusal_has_a_close_code_of_its_name(self):
        assert set(HearingRefusal.__members__) <= set(HearingClose.__members__)

    def test_call_58_every_ending_the_relay_closes_with_is_named_by_its_own_code(self):
        assert {ending.name: ending.value for ending in HopClose} == {
            name: HearingClose[name].value for name in HopClose.__members__
        }


class TestWhatTheServiceAnswers:
    @pytest.mark.parametrize(
        ("answer", "close"),
        [
            pytest.param(refused_with(RefusalCode.BUSY, headers=(("Retry-After", "2"),)), HearingClose.BUSY, id="busy"),
            pytest.param(refused_with(RefusalCode.FORBIDDEN), HearingClose.SERVICE_FAILED, id="refused"),
            pytest.param(Refusal(HTTPStatus.OK), HearingClose.SERVICE_FAILED, id="off_the_wire"),
        ],
    )
    def test_call_58_a_refused_upgrade_closes_the_browser_with_the_code_of_its_kind(
        self, sponsored, fake_speech, answer, close
    ):
        fake_speech.refusal = answer

        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            closed = closed_with(socket)

        assert closed == close

    def test_call_58_an_error_event_ends_the_hop_with_a_service_code_and_no_entry(self, sponsored, fake_speech):
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            fake_speech.next_session().say(failing(RefusalCode.UNAVAILABLE))
            closed = closed_with(socket)

        assert (closed, entries_of(sponsored)) == (HearingClose.SERVICE_FAILED, [])

    def test_call_58_a_service_that_is_down_closes_the_browser_with_a_service_code(self, sponsored, fake_speech):
        fake_speech.stop()

        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            closed = closed_with(socket)

        assert closed == HearingClose.SERVICE_FAILED

    def test_call_58_a_hop_past_its_deadline_closes_with_its_own_code(self, sponsored, fake_speech, monkeypatch):
        monkeypatch.setattr(hearing_route, "HEARING_DEADLINE_SECONDS", 0.2)

        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            closed = closed_with(socket)

        assert closed == HearingClose.PAST_DEADLINE

    def test_call_58_a_frame_that_is_text_or_too_large_ends_the_hop_with_its_code(self, sponsored, fake_speech):
        with hop(sponsored) as socket:
            socket.send_text("sound?")
            text_closed = closed_with(socket)
        with hop(sponsored) as socket:
            socket.send_bytes(bytes(hearing_route.HOP_FRAME_BYTES + 1))
            large_closed = closed_with(socket)

        assert (text_closed, large_closed) == (HearingClose.TEXT_FRAME, HearingClose.FRAME_TOO_LARGE)

    def test_call_58_more_sound_than_the_hold_allows_before_the_service_opens_ends_the_hop(
        self, sponsored, fake_speech
    ):
        """CALL-58: Sound the service has not read is held up to a few frames; one more ends the hop."""
        fake_speech.opening.clear()
        try:
            with hop(sponsored) as socket:
                for _ in range(hearing_route.HELD_FRAMES + 1):
                    socket.send_bytes(bytes(hearing_route.HOP_FRAME_BYTES))
                closed = closed_with(socket)
        finally:
            fake_speech.opening.set()

        assert closed == HearingClose.OVER_CAP


def _send_from_the_loop(socket, frame):
    """`socket.send_bytes` for a caller on the app's loop, which that call blocks; the frame is waiting for the
    relay's reader at once."""
    socket._receive_tx.send_nowait({"type": "websocket.receive", "bytes": frame})  # noqa: SLF001 - no public way in


def _remove(scene):
    scene.owner.send({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": scene.agent_id})


def _agent_leaves(scene):
    scene.call.call(scene.token, LEAVE_CALL, {"room": scene.owner.room_id})


def _lease_ends(scene):
    scene.call.pass_time(LEASE_SECONDS)


def _seat_parks(scene):
    scene.owner.drop()


def _seat_leaves(scene):
    scene.owner.drop(code=1000)


def _call_ends(scene):
    scene.owner.send({"type": SignalType.END_ROOM.value})


def _shut_down(scene):
    scene.call.shut_down()


EVERY_END = pytest.mark.parametrize(
    "end",
    [switch_off, _remove, _agent_leaves, _lease_ends, _seat_parks, _seat_leaves, _call_ends, _shut_down],
    ids=["switch_off", "removal", "leave", "lease_end", "park", "seat_leaves", "call_end", "shutdown"],
)


class TestEveryEndStopsTheHopAtOnce:
    @EVERY_END
    def test_call_58_the_browser_is_closed_in_order_the_service_is_left_and_no_later_frame_reaches_it(
        self, sponsored, fake_speech, end
    ):
        """CALL-58: Each end of hearing ends the hop at once."""
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.wait_for_audio(1)

            end(sponsored)
            closed = closed_with(socket)
            session.wait_until_closed()

        assert (closed, session.audio_appends()) == (HearingClose.NORMAL, 1)

    @EVERY_END
    def test_call_58_an_end_while_the_workload_grant_is_awaited_closes_the_browser_and_opens_no_service_session(
        self, sponsored, fake_speech, end
    ):
        """CALL-58: The hop ends at once also while the issuer is still answering for HopIn's own token."""
        hold = sponsored.call.issuer.hold(CLIENT_CREDENTIALS_GRANT)
        try:
            with hop(sponsored) as socket:
                assert hold.parked.wait(timeout=10)
                end(sponsored)
                closed = closed_with(socket)
        finally:
            hold.release.set()

        assert (closed, fake_speech.sessions) == (HearingClose.NORMAL, [])

    def test_call_58_a_sentence_that_finishes_after_the_end_is_not_appended(self, sponsored, fake_speech):
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.wait_for_audio(1)
            switch_off(sponsored)
            closed_with(socket)
            session.wait_until_closed()
            for event in a_sentence("too late"):
                session.say(event)

        assert entries_of(sponsored) == []

    def test_call_58_a_frame_already_waiting_when_the_hop_ends_does_not_reach_the_service(self, sponsored, fake_speech):
        """CALL-58: No newly accepted frame is forwarded after the end."""
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.wait_for_audio(1)

            sponsored.call.end_room_after(sponsored.owner.room_id, lambda: _send_from_the_loop(socket, SOUND))
            closed = closed_with(socket)
            session.wait_until_closed()

        assert (closed, session.audio_appends()) == (HearingClose.NORMAL, 1)

    def test_call_58_a_newer_hop_of_the_seat_ends_the_older(self, sponsored, fake_speech):
        with hop(sponsored) as older:
            older.send_bytes(SOUND)
            fake_speech.next_session(1).wait_for_audio(1)

            with hop(sponsored) as newer:
                newer.send_bytes(SOUND)
                second = fake_speech.next_session(2)
                second.wait_for_audio(1)
                older_closed = closed_with(older)
                second.hang_up()
                newer_closed = closed_with(newer)

        assert (older_closed, newer_closed) == (HearingClose.NORMAL, HearingClose.NORMAL)


def _rechecks_waiting(call):
    return [timer for timer in call.scheduler.pending_timers() if timer.delay == SESSION_RECHECK_SECONDS]


class TestTheSessionIsRechecked:
    def test_call_58_a_hop_whose_session_was_revoked_ends_at_the_next_recheck_while_the_account_keeps_its_seat(
        self, sponsored, fake_speech
    ):
        """CALL-58: Another login of the account holds the seat; revoking the hop's own session ends the hop alone."""
        call = sponsored.call
        hop_login = call.another_device(sponsored.owner_account)
        with hop(sponsored, login=hop_login) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.wait_for_audio(1)

            call.stores.sessions.delete(hop_login.session.id)
            call.pass_time(SESSION_RECHECK_SECONDS)
            closed = closed_with(socket)
            session.wait_until_closed()

        assert (closed, session.audio_appends()) == (HearingClose.NORMAL, 1)

    def test_call_58_a_hop_whose_session_still_admits_goes_on_through_the_recheck(self, sponsored, fake_speech):
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.wait_for_audio(1)

            sponsored.call.pass_time(SESSION_RECHECK_SECONDS)
            socket.send_bytes(SOUND)
            session.wait_for_audio(2)
            session.hang_up()

            assert closed_with(socket) == HearingClose.NORMAL

    def test_call_58_a_recheck_that_fails_ends_the_hop(self, sponsored, fake_speech):
        call = sponsored.call
        hop_login = call.another_device(sponsored.owner_account)
        with hop(sponsored, login=hop_login) as socket:
            socket.send_bytes(SOUND)
            fake_speech.next_session().wait_for_audio(1)

            call.stores.sessions.fail_loads_of(hop_login.session.id)
            with pytest.raises(ConnectionError):
                call.pass_time(SESSION_RECHECK_SECONDS)

            assert closed_with(socket) == HearingClose.NORMAL

    def test_call_58_a_recheck_that_starts_after_the_hop_ended_looks_nothing_up_and_asks_again_no_more(
        self, sponsored, fake_speech
    ):
        call = sponsored.call
        hop_login = call.another_device(sponsored.owner_account)
        with hop(sponsored, login=hop_login) as socket:
            socket.send_bytes(SOUND)
            fake_speech.next_session().wait_for_audio(1)
            switch_off(sponsored)
            closed_with(socket)
        looked_up = call.stores.sessions.loaded.count(hop_login.session.id)

        call.client.portal.call(call.scheduler.run_cancelled)

        assert call.stores.sessions.loaded.count(hop_login.session.id) == looked_up

    def test_call_58_a_recheck_still_running_when_the_hop_is_over_sets_no_timer_for_the_next(
        self, sponsored, fake_speech
    ):
        """The hop is over without its end having been set: the service hung up."""
        call = sponsored.call
        hop_login = call.another_device(sponsored.owner_account)
        of_the_seats = len(_rechecks_waiting(call))
        with hop(sponsored, login=hop_login) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.wait_for_audio(1)
            hold = call.stores.sessions.hold_loads_of(hop_login.session.id)
            checking = call.client.portal.start_task_soon(call.scheduler.advance, SESSION_RECHECK_SECONDS)
            assert hold.parked.wait(timeout=10)

            session.hang_up()
            closed_with(socket)
        hold.release.set()
        checking.result(timeout=10)

        assert len(_rechecks_waiting(call)) == of_the_seats


class TestHearingOff:
    @pytest.fixture
    def hearing_service(self):
        return

    def test_call_58_with_the_setting_off_there_is_no_route_and_nobody_is_called(self, scene, fake_speech):
        with pytest.raises(WebSocketDisconnect), hop(scene):
            pass

        assert (grants(scene, TOKEN_EXCHANGE_GRANT), grants(scene, CLIENT_CREDENTIALS_GRANT), fake_speech.sessions) == (
            [],
            [],
            [],
        )


def the_service_drops(session, socket):
    session.drop()
    closed_with(socket)


def the_browser_leaves(_session, socket):
    socket.close()


class TestHowAHopEndsInTheLog:
    @pytest.mark.parametrize(
        ("action", "ending"),
        [
            pytest.param(the_service_drops, "cut", id="the_service_drops"),
            pytest.param(the_browser_leaves, "browser_left", id="the_browser_leaves"),
        ],
    )
    def test_call_58_a_hop_the_service_drops_or_the_browser_leaves_is_logged_with_that_ending(
        self, sponsored, fake_speech, action, ending
    ):
        endings = []
        logged = threading.Event()

        def note_the_ending(_logger, _method, event):
            if event["event"] == "agent_hearing_ended":
                endings.append(event["outcome"])
                logged.set()
            return event

        with structlog.testing.capture_logs(processors=[note_the_ending]), hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.wait_for_audio(1)
            action(session, socket)
            assert logged.wait(WAIT_SECONDS), "the hop's ending was never logged"

        assert endings == [ending]


class TestNothingIsLogged:
    @pytest.mark.parametrize("ending", ["a_sentence", "a_refusal"])
    def test_call_58_a_debug_log_of_a_hop_carries_no_sound_no_text_and_no_token(
        self, sponsored, fake_speech, caplog, ending
    ):
        if ending == "a_refusal":
            fake_speech.refusal = refused_with(RefusalCode.FORBIDDEN)
        text = "ein geheimer Satz"

        with caplog.at_level(logging.DEBUG), structlog.testing.capture_logs() as logs, hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session() if ending == "a_sentence" else None
            if session is not None:
                for event in a_sentence(text):
                    session.say(event)
                session.hang_up()
            closed_with(socket)

        tokens = [
            *sponsored.call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT),
            *sponsored.call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT),
        ]
        sound = (SOUND.hex(), base64.b64encode(SOUND).decode())
        leaked = [needle for needle in (text, *sound, *tokens) if needle in caplog.text or needle in repr(logs)]
        assert leaked == []
