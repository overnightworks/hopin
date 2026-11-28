"""What a hearing hop writes to the log: how it ends, the transcript relay and what no log line carries."""

import base64
import logging
from http import HTTPStatus

import pytest
import structlog.testing
from logline import Outcome
from speech_client.hearing import Refused
from speech_client.outcomes import Cut, Unreachable
from speech_client.relay import HopClose, RuleBroken
from speech_client.wire import RefusalCode

from hopin.agent import hearing_route
from hopin.rooms.reference import room_reference
from tests.participation.support.hearing_scene import hear_one_sentence, switch_off
from tests.participation.support.speaking_scene import CLIENT_CREDENTIALS_GRANT
from tests.support.fake_issuer import TOKEN_EXCHANGE_GRANT
from tests.support.fake_speech import WAIT_SECONDS, a_sentence, refused_with
from tests.support.hearing_socket import SOUND, closed_with, hop
from tests.support.log_lines import captured_lines

pytestmark = [pytest.mark.integration, pytest.mark.hears_through_fake_speech]


def the_browser_leaves(_scene, _session, socket):
    socket.close()


def the_service_closes(_scene, session, socket):
    session.hang_up()
    closed_with(socket)


def the_app_ends_it(scene, _session, socket):
    switch_off(scene)
    closed_with(socket)


class TestHowAHopEndsInTheLog:
    @pytest.mark.parametrize(
        "end",
        [the_browser_leaves, the_service_closes, the_app_ends_it],
        ids=["browser_leaves", "service_closes", "app_ends"],
    )
    def test_call_58_a_hop_that_ends_as_designed_ends_its_step_line_ok_and_names_no_reason(
        self, sponsored, fake_speech, end
    ):
        """CALL-58."""
        with captured_lines() as lines, hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            session = fake_speech.next_session()
            session.wait_for_audio(1)
            end(sponsored, session, socket)
            assert lines.wait_for_event("hearing.ended", WAIT_SECONDS), "the hop's ending was never logged"

        [ending] = [line for line in lines if line["event"] == "hearing.ended"]
        assert (
            ending["outcome"],
            "reason" in ending,
            ending["room_ref"],
            isinstance(ending["duration_ms"], float),
        ) == (
            Outcome.OK,
            False,
            room_reference(sponsored.owner.room_id),
            True,
        )

    @pytest.mark.parametrize(
        ("outcome", "reason"),
        [
            (Cut(), "cut"),
            (Unreachable(), "unreachable"),
            (Refused(HTTPStatus.BAD_REQUEST, None), "refused"),
            (RuleBroken(HopClose.TEXT_FRAME), "text_frame"),
            (RuleBroken(HopClose.FRAME_TOO_LARGE), "frame_too_large"),
        ],
    )
    def test_call_58_an_outcome_that_served_nobody_is_named_by_its_closed_reason(self, outcome, reason):
        """CALL-58."""
        assert hearing_route.ending_reason(outcome) == reason


class TestTheTranscriptRelay:
    def test_call_58_a_sentence_writes_its_relay_hop_its_line_and_its_pulse_with_the_room_reference_and_no_content(
        self, sponsored, fake_speech
    ):
        """CALL-58."""
        text = "ein geheimer Satz"
        with captured_lines() as lines:
            hear_one_sentence(sponsored, fake_speech, text)

        by_event = {line["event"]: line for line in lines}
        relay, entered, pulse = (by_event[name] for name in ("transcript_relay.ended", "line.entered", "pulse.sent"))
        room_ref = room_reference(sponsored.owner.room_id)
        assert (relay["outcome"], relay["room_ref"], entered["room_ref"], entered["seq"], pulse["seq"]) == (
            Outcome.OK,
            room_ref,
            room_ref,
            1,
            1,
        )
        assert (entered["agent_id"], entered["kind"], isinstance(relay["duration_ms"], float)) == (
            pulse["agent_id"],
            "heard",
            True,
        )
        assert (text in repr(lines), sponsored.owner.room_id in repr(lines)) == (False, False)


class TestNothingIsLogged:
    @pytest.mark.parametrize("ending", ["a_sentence", "a_refusal"])
    def test_call_58_a_debug_log_of_a_hop_carries_no_sound_no_text_and_no_token(
        self, sponsored, fake_speech, caplog, ending
    ):
        """CALL-58."""
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
