"""CALL-69: a seat reports how far its player stood in the held sentence; the server checks the report, bounds it by its
own clock, keeps it with the hold and writes one line when the hold ends."""

import asyncio
import json
import math

import pytest
from logline.testing import configured

from hopin.config.constants import ErrorReason, SignalType
from hopin.participation.vocabulary import HoldEnd, ParticipationSignal, VoiceFailure
from hopin.rooms.reference import room_reference
from tests.participation.support.people import person_named, room_of
from tests.participation.support.speaking import (
    LONG,
    StreamedSentence,
    rendering,
    speak,
    voice_ids,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GUEST = person_named("guest")
HOLD_ENDED_EVENT = "agent_hold.ended"
PLAYED_BEFORE_THE_HOLD_SECONDS = 0.5


@pytest.fixture
def hears():
    return True


def report(agent, voice_id, played_seconds):
    return {
        "type": ParticipationSignal.AGENT_HOLD_PLAYED.value,
        "agent_id": agent.agent_id,
        "voice_id": voice_id,
        "played_seconds": played_seconds,
    }


def hold_lines(capsys):
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    return [line for line in lines if line["event"] == HOLD_ENDED_EVENT]


async def held_after_playing(participation, call, scheduler):
    """The call's room where the agent played a long sentence for half a second when the guest started speaking, and
    the id of the sentence's voice."""
    host, *_ = call
    room_id = room_of(host)
    await speak(participation, room_id=room_id, render=LONG)
    await scheduler.advance(PLAYED_BEFORE_THE_HOLD_SECONDS)
    await participation.person_started(room_id, call[1].peer_id)
    return room_id, voice_ids(host)[0]


async def line_of_the_hold_continued(participation, room_id, capsys):
    await participation.continue_speaking(GUEST, room_id, "1")
    [line] = hold_lines(capsys)
    return line


async def a_second_hold_stopped(participation, room_id, guest):
    participation.speech.turn_ended(room_id, guest.peer_id)
    await participation.person_started(room_id, guest.peer_id)
    await participation.stop_speaking(GUEST, room_id, "2")


class TestTheServerChecksTheReport:
    @pytest.mark.parametrize(
        "played_seconds",
        [
            pytest.param(True, id="a boolean"),
            pytest.param("1", id="a string"),
            pytest.param(None, id="none"),
            pytest.param(-0.1, id="negative"),
            pytest.param(math.inf, id="infinite"),
            pytest.param(math.nan, id="not a number"),
            pytest.param(10**400, id="an integer too large for a float"),
        ],
    )
    async def test_call_69_a_played_time_that_is_no_finite_non_negative_number_is_an_invalid_message_and_counts_not(
        self, participation, call, scheduler, capsys, played_seconds
    ):
        host, _, mozart, _ = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)

            await host.say(report(mozart, voice_id, played_seconds))
            line = await line_of_the_hold_continued(participation, room_id, capsys)

        assert (host.frames(SignalType.ERROR)[-1]["reason"], line["seat_report_count"]) == (
            ErrorReason.INVALID_MESSAGE.value,
            0,
        )

    async def test_call_69_a_report_without_its_agent_or_voice_is_an_invalid_message(
        self, participation, call, scheduler
    ):
        host, _, _, _ = call
        await held_after_playing(participation, call, scheduler)

        await host.say({"type": ParticipationSignal.AGENT_HOLD_PLAYED.value, "played_seconds": 0.1})

        assert host.frames(SignalType.ERROR)[-1]["reason"] == ErrorReason.INVALID_MESSAGE.value

    @pytest.mark.parametrize("seconds", [0, 0.25, 3], ids=["zero", "a fraction", "an integer above the clock"])
    async def test_call_69_a_valid_report_is_kept_with_the_hold_and_answered_with_no_frame(
        self, participation, call, scheduler, capsys, seconds
    ):
        host, _, mozart, _ = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)
            sent_before = [len(socket.sent) for socket in host.sockets]

            await host.say(report(mozart, voice_id, seconds))
            sent_after = [len(socket.sent) for socket in host.sockets]
            line = await line_of_the_hold_continued(participation, room_id, capsys)

        assert (sent_after, line["seat_report_count"]) == (sent_before, 1)

    async def test_call_69_a_report_above_the_servers_clock_for_the_sentence_is_cut_down_to_it_and_counted_as_cut(
        self, participation, call, scheduler, capsys
    ):
        host, _, mozart, _ = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)

            await host.say(report(mozart, voice_id, 9))
            line = await line_of_the_hold_continued(participation, room_id, capsys)

        assert (line["seat_played_min_ms"], line["seat_report_cut_count"]) == (500, 1)

    async def test_call_69_a_report_within_the_clock_is_not_cut(self, participation, call, scheduler, capsys):
        host, _, mozart, _ = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)

            await host.say(report(mozart, voice_id, 0.25))
            line = await line_of_the_hold_continued(participation, room_id, capsys)

        assert (line["seat_played_min_ms"], line["seat_report_cut_count"]) == (250, 0)

    async def test_call_69_the_line_names_the_smallest_report_of_the_seats(
        self, participation, call, scheduler, capsys
    ):
        host, guest, mozart, _ = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)

            await host.say(report(mozart, voice_id, 0.4))
            await guest.say(report(mozart, voice_id, 0.1))
            line = await line_of_the_hold_continued(participation, room_id, capsys)

        assert (line["seat_report_count"], line["seat_played_min_ms"]) == (2, 100)

    async def test_call_69_a_second_report_of_one_seat_for_the_hold_is_dropped(
        self, participation, call, scheduler, capsys
    ):
        host, _, mozart, _ = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)

            await host.say(report(mozart, voice_id, 0.4))
            await host.say(report(mozart, voice_id, 0.1))
            line = await line_of_the_hold_continued(participation, room_id, capsys)

        assert (line["seat_report_count"], line["seat_played_min_ms"]) == (1, 400)

    @pytest.mark.parametrize(
        "named",
        [
            pytest.param("agent", id="an agent that is not joined in the room"),
            pytest.param("voice", id="a voice that is not the held sentence's"),
        ],
    )
    async def test_call_69_a_report_naming_another_agent_or_voice_is_dropped_without_an_answer(
        self, participation, call, scheduler, capsys, named
    ):
        host, _, mozart, _ = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)
            message = report(mozart, voice_id, 0.1)
            message["agent_id" if named == "agent" else "voice_id"] = "unknown"
            sent_before = [len(socket.sent) for socket in host.sockets]

            await host.say(message)
            sent_after = [len(socket.sent) for socket in host.sockets]
            line = await line_of_the_hold_continued(participation, room_id, capsys)

        assert (sent_after, line["seat_report_count"]) == (sent_before, 0)

    async def test_call_69_a_report_for_an_agent_that_is_joined_but_not_held_is_dropped(
        self, participation, call, scheduler, capsys
    ):
        host, _, _, salieri = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)

            await host.say(report(salieri, voice_id, 0.1))
            line = await line_of_the_hold_continued(participation, room_id, capsys)

        assert (line["seat_report_count"], host.frames(SignalType.ERROR)) == (0, [])

    async def test_call_69_a_report_after_the_hold_ended_is_dropped_and_counts_for_no_later_hold(
        self, participation, call, scheduler, capsys
    ):
        host, guest, mozart, _ = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)
            await line_of_the_hold_continued(participation, room_id, capsys)
            await host.say(report(mozart, voice_id, 0.1))

            await a_second_hold_stopped(participation, room_id, guest)

        [line] = hold_lines(capsys)
        assert (line["seat_report_count"], host.frames(SignalType.ERROR)) == (0, [])

    async def test_call_69_a_report_for_a_hold_with_no_started_sentence_is_dropped(self, participation, call, capsys):
        host, guest, mozart, _ = call
        room_id = room_of(host)
        rendered = asyncio.Event()

        async def still_rendering(text, voice):
            await rendered.wait()
            return await rendering()(text, voice)

        pending = asyncio.create_task(participation.speak(GUEST, room_id, "Eins", still_rendering))
        try:
            await asyncio.sleep(0)
            with configured("hopin"):
                await participation.person_started(room_id, guest.peer_id)

                await host.say(report(mozart, "any", 0.1))
                await participation.stop_speaking(GUEST, room_id, "1")

            [line] = hold_lines(capsys)
            assert (line["seat_report_count"], "played_ms" in line) == (0, False)
        finally:
            pending.cancel()

    async def test_call_69_a_report_for_a_sentence_a_voice_failure_cut_is_dropped(self, participation, call, capsys):
        host, guest, mozart, _ = call
        room_id = room_of(host)
        streamed = StreamedSentence()
        await speak(participation, "Eins", room_id=room_id, render=streamed.render)
        voice_id = voice_ids(host)[0]
        with configured("hopin"):
            await participation.person_started(room_id, guest.peer_id)
            await streamed.breaks_off(VoiceFailure.CUT)

            await host.say(report(mozart, voice_id, 0.1))
            await participation.stop_speaking(GUEST, room_id, "1")

        [line] = hold_lines(capsys)
        assert (line["seat_report_count"], "played_ms" in line) == (0, False)


class TestAHoldWritesOneLineWhenItEnds:
    @pytest.mark.parametrize(
        ("ending", "end"),
        [
            pytest.param(
                HoldEnd.CONTINUED,
                lambda participation, room_id, guest: participation.continue_speaking(GUEST, room_id, "1"),
                id="a continue",
            ),
            pytest.param(
                HoldEnd.STOPPED,
                lambda participation, room_id, guest: participation.stop_speaking(GUEST, room_id, "1"),
                id="a stop",
            ),
            pytest.param(
                HoldEnd.ANSWERED,
                lambda participation, room_id, guest: speak(participation, "Zwei", room_id=room_id, interrupted="1"),
                id="a speak that names the hold",
            ),
            pytest.param(HoldEnd.ENDED, lambda participation, room_id, guest: guest.drop(), id="the agent's end"),
        ],
    )
    async def test_call_69_each_way_a_hold_ends_writes_one_line_with_its_reason_and_ids_and_numbers_only(
        self, participation, call, scheduler, capsys, ending, end
    ):
        host, guest, mozart, _ = call
        with configured("hopin"):
            room_id, voice_id = await held_after_playing(participation, call, scheduler)
            await host.say(report(mozart, voice_id, 0.3))

            await end(participation, room_id, guest)

        [line] = hold_lines(capsys)
        assert {
            key: line[key]
            for key in (
                "room_ref",
                "agent_id",
                "reason",
                "outcome",
                "played_ms",
                "seat_report_count",
                "seat_played_min_ms",
                "seat_report_cut_count",
            )
        } == {
            "room_ref": room_reference(room_id),
            "agent_id": mozart.agent_id,
            "reason": ending.value,
            "outcome": "ok",
            "played_ms": 500,
            "seat_report_count": 1,
            "seat_played_min_ms": 300,
            "seat_report_cut_count": 0,
        }
        assert isinstance(line["duration_ms"], float)

    async def test_call_69_a_hold_nobody_reported_leaves_the_seat_minimum_out(
        self, participation, call, scheduler, capsys
    ):
        with configured("hopin"):
            room_id, _ = await held_after_playing(participation, call, scheduler)

            line = await line_of_the_hold_continued(participation, room_id, capsys)

        assert ("seat_played_min_ms" in line, line["seat_report_count"]) == (False, 0)

    async def test_call_69_a_hold_ends_once_and_a_later_hold_writes_its_own_line(
        self, participation, call, scheduler, capsys
    ):
        with configured("hopin"):
            room_id, _ = await held_after_playing(participation, call, scheduler)
            await participation.continue_speaking(GUEST, room_id, "1")
            await a_second_hold_stopped(participation, room_id, call[1])

        assert [line["reason"] for line in hold_lines(capsys)] == [HoldEnd.CONTINUED, HoldEnd.STOPPED]
