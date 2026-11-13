"""CALL-54 and CALL-60: the record of people talking over an agent's sentence, at participation's real feature and the
agents library's real record of who may hear whom."""

import pytest
import pytest_asyncio

from hopin.participation.chat_window import LineKind, WindowLine
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import LONG, SENTENCE_MS, speak, waiting_to_speak
from tests.signaling.support.browsers import host_and_guest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

HOST = person_named("host")
GUEST = person_named("guest")


@pytest.fixture
def hears():
    return True


@pytest_asyncio.fixture
async def call(link, participation):
    """A host and a guest in one call, each with an agent of their own that hears them."""
    host, guest = await host_and_guest(link)
    mozart = await participation.dial_in(GUEST, room_of(guest), "Mozart")
    await participation.dial_in(HOST, room_of(host), "Salieri")
    return host, guest, mozart


def window_of(participation, person, room_id):
    return participation.read_window(person, room_id)


def record_of(participation, person, room_id):
    record = window_of(participation, person, room_id).talked_over
    return None if record is None else (record.id, record.playing, [(p.name, p.text) for p in record.speech])


class TestAPersonTalksOverASentence:
    async def test_call_54_a_person_the_agent_hears_starting_to_speak_while_its_sentence_plays_fills_talked_over(
        self, participation, call
    ):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)

        participation.speech.person_started(room_id, guest.peer_id)

        assert record_of(participation, GUEST, room_id) == ("1", True, [("guest", "")])

    async def test_call_54_only_the_agent_that_hears_the_person_is_talked_over(self, participation, call):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await speak(participation, room_id=room_id, render=LONG, who=HOST)

        participation.speech.person_started(room_id, guest.peer_id)

        assert [record_of(participation, person, room_id) for person in (GUEST, HOST)] == [
            ("1", True, [("guest", "")]),
            None,
        ]

    async def test_call_54_a_person_the_sponsor_switched_the_agents_hearing_off_for_does_not_talk_it_over(
        self, participation, call
    ):
        host, guest, mozart = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await guest.say(
            {
                "type": ParticipationSignal.AGENT_HEARING.value,
                "agent_id": mozart.agent_id,
                "on": False,
            }
        )

        participation.speech.person_started(room_id, guest.peer_id)

        assert record_of(participation, GUEST, room_id) is None

    async def test_call_54_the_words_heard_so_far_update_the_partial(self, participation, call):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        participation.speech.person_started(room_id, guest.peer_id)

        participation.speech.words_heard(room_id, guest.peer_id, "Moment")

        assert record_of(participation, GUEST, room_id) == ("1", True, [("guest", "Moment")])

    async def test_call_54_a_finished_line_replaces_the_partial_in_the_same_change_of_the_window(
        self, participation, call, window_changes
    ):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        participation.speech.person_started(room_id, guest.peer_id)
        participation.speech.words_heard(room_id, guest.peer_id, "Moment")
        window_changes.clear()

        participation.heard(guest.peer_id, "Moment bitte", began_at=0.0)

        window = window_of(participation, GUEST, room_id)
        assert (window.talked_over, [line.text for line in window.lines], window_changes) == (
            None,
            ["Moment bitte"],
            [(GUEST, room_id)],
        )

    async def test_call_54_a_turn_without_words_ends_the_record_and_the_next_has_a_new_id(self, participation, call):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        participation.speech.person_started(room_id, guest.peer_id)

        participation.speech.turn_ended(room_id, guest.peer_id)
        gone = record_of(participation, GUEST, room_id)
        participation.speech.person_started(room_id, guest.peer_id)

        assert (gone, record_of(participation, GUEST, room_id)) == (None, ("2", True, [("guest", "")]))

    async def test_call_54_a_turn_that_began_before_the_sentence_makes_no_record(self, participation, call):
        host, guest, _ = call
        room_id = room_of(host)
        participation.speech.person_started(room_id, guest.peer_id)

        await speak(participation, room_id=room_id, render=LONG)
        participation.speech.words_heard(room_id, guest.peer_id, "Hallo")

        assert record_of(participation, GUEST, room_id) is None

    async def test_call_54_the_sponsor_switching_the_agents_hearing_off_takes_the_partial_away(
        self, participation, call
    ):
        host, guest, mozart = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        participation.speech.person_started(room_id, guest.peer_id)

        await guest.say(
            {
                "type": ParticipationSignal.AGENT_HEARING.value,
                "agent_id": mozart.agent_id,
                "on": False,
            }
        )

        assert record_of(participation, GUEST, room_id) is None

    async def test_call_54_a_window_change_is_told_for_the_start_and_for_the_turn_end(
        self, participation, call, window_changes
    ):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        window_changes.clear()

        participation.speech.person_started(room_id, guest.peer_id)
        participation.speech.turn_ended(room_id, guest.peer_id)

        assert window_changes == [(GUEST, room_id), (GUEST, room_id)]


class TestTheRecordFollowsTheSentence:
    async def test_call_54_playing_turns_false_in_the_change_in_which_the_sentence_enters_the_window(
        self, participation, call, scheduler
    ):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, "Guten Tag", room_id=room_id)
        participation.speech.person_started(room_id, guest.peer_id)

        await scheduler.advance(SENTENCE_MS / 1000)

        window = window_of(participation, GUEST, room_id)
        assert (window.talked_over.playing, window.lines) == (
            False,
            (WindowLine(1, LineKind.AGENT, "Mozart", "Guten Tag", own=True, spoken=True),),
        )

    async def test_call_54_playing_stays_true_when_the_agents_next_sentence_starts_at_once(
        self, participation, call, scheduler
    ):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)
        await waiting_to_speak(participation, "Zwei", room_id=room_id)
        participation.speech.person_started(room_id, guest.peer_id)

        await scheduler.advance(SENTENCE_MS / 1000)

        window = window_of(participation, GUEST, room_id)
        assert (window.talked_over.playing, [line.text for line in window.lines]) == (True, ["Eins"])

    async def test_call_54_playing_turns_true_again_when_a_sentence_starts_while_the_record_stands(
        self, participation, call, scheduler
    ):
        host, guest, _ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id)
        participation.speech.person_started(room_id, guest.peer_id)
        await scheduler.advance(SENTENCE_MS / 1000)

        await speak(participation, "Zwei", room_id=room_id)

        assert record_of(participation, GUEST, room_id) == ("1", True, [("guest", "")])
