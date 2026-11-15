"""CALL-58: the reopened hearing session, at HopIn's real `/ws` with a fake speech service and issuer."""

import pytest
from speech_client.wire import WORKLOAD_TOKEN_HEADER, RefusalCode, VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.participation.vocabulary import HearingClose
from tests.participation.support.hearing_scene import (
    AUDIENCE_SCOPE,
    entries_of,
    header_of,
    logged_endings,
    lose_the_held_token,
)
from tests.participation.support.speaking_scene import CLIENT_CREDENTIALS_GRANT, grants
from tests.support.fake_issuer import TOKEN_LIFETIME_SECONDS
from tests.support.fake_speech import a_sentence, refused_with
from tests.support.hearing_socket import SOUND, closed_with, hop

pytestmark = pytest.mark.integration


@pytest.fixture
def hearing_service(fake_speech):
    return SpeechService(fake_speech.origin, VoiceId.THORSTEN, AUDIENCE_SCOPE)


class TestAReopenedSession:
    """CALL-58: The speech client reopens a hearing the service drained or cut; the route hands it a token source that
    answers afresh on every opening."""

    def test_call_58_a_session_the_service_cut_is_reopened_and_each_sentence_enters_the_window_once(
        self, sponsored, fake_speech
    ):
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

    def test_call_58_a_reopening_the_issuer_refuses_ends_the_hearing_in_order_with_no_token_logged(
        self, sponsored, fake_speech, issuer_clock
    ):
        with logged_endings() as endings, hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            first = fake_speech.next_session(1)
            first.wait_for_audio(1)
            issuer_clock.advance(TOKEN_LIFETIME_SECONDS)
            sponsored.call.issuer.refuses_client_credentials_grants = True
            first.drop()
            closed = closed_with(socket)

        assert (closed, endings, len(fake_speech.sessions)) == (HearingClose.NORMAL, ["no_token"], 1)

    def test_call_58_a_reopening_after_the_agents_token_is_gone_ends_the_hearing_in_order_with_no_token_logged(
        self, sponsored, fake_speech
    ):
        with logged_endings() as endings, hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            first = fake_speech.next_session(1)
            first.wait_for_audio(1)
            lose_the_held_token(sponsored)
            first.drop()
            closed = closed_with(socket)

        assert (closed, endings, len(fake_speech.sessions)) == (HearingClose.NORMAL, ["no_token"], 1)

    def test_call_58_a_service_that_stays_unavailable_ends_the_hearing_with_a_service_code_logged_as_reconnect_failed(
        self, sponsored, fake_speech
    ):
        with logged_endings() as endings, hop(sponsored) as socket:
            socket.send_bytes(SOUND)
            first = fake_speech.next_session(1)
            first.wait_for_audio(1)
            fake_speech.refusal = refused_with(RefusalCode.UNAVAILABLE)
            first.drop()
            closed = closed_with(socket)

        assert (closed, endings) == (HearingClose.SERVICE_FAILED, ["reconnect_failed"])
