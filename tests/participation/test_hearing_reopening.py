"""The reopened hearing session, at HopIn's real `/ws` with a fake speech service and issuer."""

import pytest
from speech_client.wire import WORKLOAD_TOKEN_HEADER, RefusalCode

from hopin.participation.vocabulary import HearingClose
from tests.participation.support.hearing_scene import (
    endings_of,
    entries_of,
    header_of,
    lose_the_held_token,
)
from tests.participation.support.speaking_scene import CLIENT_CREDENTIALS_GRANT, grants
from tests.support.fake_issuer import TOKEN_LIFETIME_SECONDS
from tests.support.fake_speech import a_sentence, refused_with
from tests.support.hearing_socket import SOUND, closed_with, hop
from tests.support.log_lines import captured_lines

pytestmark = [pytest.mark.integration, pytest.mark.hears_through_fake_speech]


@pytest.fixture(params=["issuer", "held_token"])
def token_lost(request, issuer_clock):
    """How a scene loses the token a reopening needs: HopIn's own token lapses and the issuer refuses the next one, or
    the agent's token lapses after the issuer refuses its exchanges."""
    if request.param == "held_token":
        return lose_the_held_token

    def refuse_the_workload_grant(scene):
        issuer_clock.advance(TOKEN_LIFETIME_SECONDS)
        scene.call.issuer.refuses_client_credentials_grants = True

    return refuse_the_workload_grant


class TestAReopenedSession:
    """The speech client reopens a hearing the service drained or cut; the route hands it a token source that
    answers afresh on every opening."""

    def test_call_58_a_session_the_service_cut_is_reopened_and_each_sentence_enters_the_window_once(
        self, sponsored, fake_speech
    ):
        """CALL-58."""
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            first = fake_speech.next_session(1)
            for event in a_sentence("erster Satz"):
                first.say(event)
            first.drop()
            second = fake_speech.next_session(2)
            for event in a_sentence("zweiter Satz"):
                second.say(event)
            second.hang_up()
            closed = closed_with(socket)

        assert (closed, [entry["text"] for entry in entries_of(sponsored)]) == (
            HearingClose.NORMAL,
            ["erster Satz", "zweiter Satz"],
        )

    def test_call_58_the_reopening_carries_the_workload_token_fetched_anew_once_the_first_lapsed(
        self, sponsored, fake_speech, issuer_clock
    ):
        """CALL-58."""
        with hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            fake_speech.next_session(1).wait_for_audio(1)
            issuer_clock.advance(TOKEN_LIFETIME_SECONDS)
            fake_speech.next_session(1).drop()
            second = fake_speech.next_session(2)
            second.hang_up()
            closed_with(socket)

        assert {header_of(session, WORKLOAD_TOKEN_HEADER) for session in fake_speech.sessions} == set(
            sponsored.call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT)
        )
        assert len(grants(sponsored, CLIENT_CREDENTIALS_GRANT)) == 2

    def test_call_58_a_reopening_without_a_token_ends_the_hearing_in_order_with_no_token_logged(
        self, sponsored, fake_speech, token_lost
    ):
        """CALL-58."""
        with captured_lines() as lines, hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            first = fake_speech.next_session(1)
            first.wait_for_audio(1)
            token_lost(sponsored)
            first.drop()
            closed = closed_with(socket)

        assert (closed, endings_of(lines), len(fake_speech.sessions)) == (HearingClose.NORMAL, ["no_token"], 1)

    def test_call_58_a_service_that_stays_unavailable_ends_the_hearing_with_a_service_code_logged_as_reconnect_failed(
        self, sponsored, fake_speech
    ):
        """CALL-58."""
        with captured_lines() as lines, hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            first = fake_speech.next_session(1)
            first.wait_for_audio(1)
            fake_speech.refusal = refused_with(RefusalCode.UNAVAILABLE)
            first.drop()
            closed = closed_with(socket)

        assert (closed, endings_of(lines)) == (HearingClose.SERVICE_FAILED, ["reconnect_failed"])
