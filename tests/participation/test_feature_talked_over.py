"""The record of people talking over an agent's sentence, at participation's real feature and the
agents library's real record of who may hear whom."""

import pytest

from hopin.participation.speaking import Turn
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.hearing_scene import heard
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import LONG, SENTENCE_MS, renders_failing, speak, waiting_to_speak

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.hears]

HOST = person_named("host")
GUEST = person_named("guest")


def window_of(participation, person, room_id):
    return participation.read_window(person, room_id)


def window_changes_of(window_changes, person):
    return [change for change in window_changes if change[0] == person]


def record_of(participation, person, room_id):
    record = window_of(participation, person, room_id).talked_over
    return None if record is None else (record.id, record.playing, [(p.name, p.text) for p in record.speech])


class TestAPersonTalksOverASentence:
    async def test_call_54_a_person_the_agent_hears_starting_to_speak_while_its_sentence_plays_fills_talked_over(
        self, participation, call
    ):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)

        await participation.person_started(room_id, guest.peer_id)

        assert record_of(participation, GUEST, room_id) == ("1", False, [("guest", "")])

    async def test_call_54_only_the_agent_that_hears_the_person_is_talked_over(self, participation, call):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await speak(participation, room_id=room_id, render=LONG, who=HOST)

        await participation.person_started(room_id, guest.peer_id)

        assert [record_of(participation, person, room_id) for person in (GUEST, HOST)] == [
            ("1", False, [("guest", "")]),
            None,
        ]

    async def test_call_54_a_person_the_sponsor_switched_the_agents_hearing_off_for_does_not_talk_it_over(
        self, participation, call
    ):
        """CALL-54."""
        host, guest, mozart, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await guest.say(
            {
                "type": ParticipationSignal.AGENT_HEARING.value,
                "agent_id": mozart.agent_id,
                "on": False,
            }
        )

        await participation.person_started(room_id, guest.peer_id)

        assert record_of(participation, GUEST, room_id) is None

    async def test_call_54_the_words_heard_so_far_update_the_partial(self, participation, call):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)

        participation.speech.words_heard(room_id, guest.peer_id, "Moment")

        assert record_of(participation, GUEST, room_id) == ("1", False, [("guest", "Moment")])

    async def test_call_54_a_finished_line_replaces_the_partial_in_the_same_change_of_the_window(
        self, participation, call, window_changes
    ):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)
        participation.speech.words_heard(room_id, guest.peer_id, "Moment")
        window_changes.clear()

        await heard(participation, guest.peer_id, "Moment bitte", began_at=0.0)

        window = window_of(participation, GUEST, room_id)
        assert (window.talked_over, [line.text for line in window.lines], window_changes) == (
            None,
            ["Moment bitte"],
            [(GUEST, room_id)],
        )

    async def test_call_54_a_turn_without_words_ends_the_record_and_the_next_has_a_new_id(self, participation, call):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)

        participation.speech.turn_ended(room_id, guest.peer_id)
        gone = record_of(participation, GUEST, room_id)
        await participation.person_started(room_id, guest.peer_id)

        assert (gone, record_of(participation, GUEST, room_id)) == (None, ("2", False, [("guest", "")]))

    async def test_call_54_a_turn_that_began_before_the_sentence_makes_no_record(self, participation, call):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await participation.person_started(room_id, guest.peer_id)

        await speak(participation, room_id=room_id, render=LONG)
        participation.speech.words_heard(room_id, guest.peer_id, "Hallo")

        assert record_of(participation, GUEST, room_id) is None

    async def test_call_54_a_turn_that_begins_while_the_agent_is_silent_is_still_told_to_its_floor(
        self, participation, call, scheduler
    ):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id)
        await scheduler.advance(SENTENCE_MS / 1000)
        told = []

        def hear(agent, seat, turn, text):
            told.append((seat, turn))
            return False

        participation.speech.hear_turns_through(hear)

        await participation.person_started(room_id, guest.peer_id)

        assert (record_of(participation, GUEST, room_id), (guest.peer_id, Turn.BEGAN) in told) == (None, True)

    async def test_call_54_the_sponsor_switching_the_agents_hearing_off_takes_the_partial_away(
        self, participation, call
    ):
        """CALL-54."""
        host, guest, mozart, _ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)

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
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, room_id=room_id, render=LONG)
        window_changes.clear()

        await participation.person_started(room_id, guest.peer_id)
        participation.speech.turn_ended(room_id, guest.peer_id)

        assert window_changes == [(GUEST, room_id), (GUEST, room_id)]


class TestTheRecordFollowsTheSentence:
    async def test_call_54_playing_turns_true_again_when_a_sentence_starts_while_the_record_stands(
        self, participation, call
    ):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)

        await speak(participation, "Zwei", room_id=room_id, interrupted="1")

        assert record_of(participation, GUEST, room_id) == ("1", True, [("guest", "")])

    async def test_call_54_playing_turns_false_in_the_change_in_which_the_sentence_enters_the_window(
        self, participation, call, scheduler
    ):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)
        await speak(participation, "Zwei", room_id=room_id, interrupted="1")

        await scheduler.advance(SENTENCE_MS / 1000)

        window = window_of(participation, GUEST, room_id)
        assert (window.talked_over.playing, [line.text for line in window.lines]) == (False, ["Eins", "Zwei"])

    async def test_call_54_playing_stays_true_when_the_agents_next_sentence_starts_at_once(
        self, participation, call, scheduler
    ):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)
        await speak(participation, "Zwei", room_id=room_id, interrupted="1")
        await waiting_to_speak(participation, "Drei", room_id=room_id)

        await scheduler.advance(SENTENCE_MS / 1000)

        window = window_of(participation, GUEST, room_id)
        assert (window.talked_over.playing, [line.text for line in window.lines]) == (True, ["Eins", "Zwei"])

    async def test_call_54_a_sentence_that_starts_at_once_after_a_continued_one_changes_the_window_only_for_the_line(
        self, participation, call, scheduler, window_changes
    ):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)
        await speak(participation, "Zwei", room_id=room_id, interrupted="1")
        await waiting_to_speak(participation, "Drei", room_id=room_id)
        window_changes.clear()

        await scheduler.advance(SENTENCE_MS / 1000)

        assert window_changes_of(window_changes, GUEST) == [(GUEST, room_id)]

    async def test_call_54_a_written_sentence_between_keeps_the_start_at_once(
        self, participation, call, scheduler, window_changes
    ):
        """CALL-54."""
        host, guest, *_ = call
        room_id = room_of(host)
        await speak(participation, "Eins", room_id=room_id, render=LONG)
        await participation.person_started(room_id, guest.peer_id)
        await speak(participation, "Zwei", room_id=room_id, interrupted="1")
        await waiting_to_speak(participation, "Drei", room_id=room_id, render=renders_failing)
        await waiting_to_speak(participation, "Vier", room_id=room_id)
        window_changes.clear()

        await scheduler.advance(SENTENCE_MS / 1000)

        assert window_changes_of(window_changes, GUEST) == [(GUEST, room_id)] * 2
