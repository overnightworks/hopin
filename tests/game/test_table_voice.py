import pytest
import structlog.testing

from hopin.game.quiz import QUIZ_ROUNDS
from hopin.game.table import Line
from hopin.game.vocabulary import VOICE_DEADLINE_SECONDS, GameRefusal, GameSignal
from tests.game.support.fake_game_master import DEFAULT_QUESTION
from tests.game.support.fake_voice import DEFAULT_WAV, FakeVoice
from tests.game.support.table_world import (
    END,
    END_ROOM,
    IDLE,
    REVEAL,
    START,
    answer,
    answer_count,
    answering,
    asked,
    assert_no_driver_failed,
    call_of,
    game_frames,
    let_every_task_run,
    line,
    question,
    refused,
    sent,
    sent_to_all,
    shut_down_within_cap,
    thinking,
    wait_within_cap,
    yield_until,
)

pytestmark = pytest.mark.asyncio

REVEAL_LINE = "Lissabon."
FINAL_LINE = "Ihr wart alle großartig."
VOICE_TYPE = GameSignal.GAME_VOICE.value


@pytest.fixture
def voice():
    voice = FakeVoice()
    yield voice
    voice.release_every_hold()


def voiced(frame):
    return {**frame, "voiced": True}


def voice_frames(browser):
    return [frame for frame in game_frames(browser) if frame["type"] == VOICE_TYPE]


def voice_ids(browser):
    return [frame["id"] for frame in voice_frames(browser) if "id" in frame]


def shown(browser):
    """The game frames the browser was sent, a voice frame without its id and a line without its voiced mark."""
    return [
        {"type": VOICE_TYPE}
        if frame["type"] == VOICE_TYPE
        else {key: value for key, value in frame.items() if key != "voiced"}
        for frame in game_frames(browser)
    ]


async def voice_sent(browsers, times=1):
    for browser in browsers:
        await browser.socket.wait_until(lambda browser=browser: len(voice_ids(browser)) >= times)


async def end_by_tap(call, starter, link):
    await starter.say(END)


async def end_by_leave(call, starter, link):
    await starter.drop(code=1000)


async def end_by_room(call, starter, link):
    await call[0].say(END_ROOM)


async def end_by_shutdown(call, starter, link):
    await shut_down_within_cap(link.handler)


class TestTheVoiceOfALine:
    async def test_every_seat_that_got_the_line_is_sent_its_voice_after_the_line_and_its_phase(
        self, link, game_master, voice, voices
    ):
        """GM-32: Every seat that got the line is sent its voice after the line and its phase."""
        call = await call_of(link, "host", "guest", "third")
        starter = call[1]

        await starter.say(START)
        await voice_sent(call)

        for browser in call:
            assert shown(browser) == [*asked(starter), {"type": VOICE_TYPE}]
        [spoken] = voice.calls
        assert (spoken.starter, spoken.text) == (starter.socket, DEFAULT_QUESTION)
        [shared_id] = {voice_id for browser in call for voice_id in voice_ids(browser)}
        assert voices.get(shared_id) == DEFAULT_WAV

    async def test_a_seat_that_joined_after_the_line_is_sent_no_voice(self, link, voice):
        """GM-32: A seat that joined after the line is sent no voice."""
        host, guest = await call_of(link, "host", "guest")
        held = voice.hold(DEFAULT_QUESTION)
        await host.say(START)
        await sent_to_all([host, guest], answering(host))
        late = await link.join("late", host.socket.sent[0]["room_id"])

        held.release.set()
        await voice_sent([host, guest])
        await let_every_task_run()

        assert voice_ids(late) == []

    async def test_a_seat_that_left_before_the_voice_is_sent_none(self, link, voice):
        """GM-32: A seat that left the call before the voice is sent gets none."""
        host, guest, third = await call_of(link, "host", "guest", "third")
        held = voice.hold(DEFAULT_QUESTION)
        await host.say(START)
        await sent_to_all([host, guest, third], answering(host))
        await third.drop(code=1000)

        held.release.set()
        await voice_sent([host, guest])
        await let_every_task_run()

        assert voice_ids(third) == []

    async def test_a_voice_arrives_once_for_each_line_the_quiz_speaks(self, link, game_master, voice):
        """GM-32: A voice arrives once for each line the quiz speaks."""
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(Line("Frage?"), Line(REVEAL_LINE))

        await host.say(START)
        await sent_to_all([host, guest], answering(host))
        await voice_sent([host, guest])
        await host.say(REVEAL)
        await guest.socket.wait_until(lambda: len(voice_ids(guest)) == 2)

        assert [call.text for call in voice.calls] == ["Frage?", REVEAL_LINE]
        assert len(set(voice_ids(guest))) == 2


class TestAVoiceThatTheTextDoesNotWaitFor:
    async def test_the_line_and_its_phase_are_sent_and_an_answer_is_taken_while_the_voice_is_made(self, link, voice):
        """GM-33: The line and its phase are sent and an answer is taken while the voice is made."""
        host, guest = await call_of(link, "host", "guest")
        voice.hold(DEFAULT_QUESTION)

        await host.say(START)
        await sent_to_all([host, guest], answering(host))
        await guest.say(answer("Lissabon"))
        await sent_to_all([host, guest], answer_count(1, 2))

        assert voice_ids(host) == []

    async def test_the_next_run_starts_only_after_the_previous_lines_voice_was_sent(self, link, game_master, voice):
        """GM-33: The run for the next line starts only after the previous line's voice was sent."""
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(Line("Frage?"), Line(REVEAL_LINE), Line("Zweite?"))
        held = voice.hold(REVEAL_LINE)

        await host.say(START)
        await sent_to_all([host, guest], answering(host))
        await host.say(REVEAL)
        await wait_within_cap(held.started)
        await let_every_task_run()
        asked_while_voice_was_made = len(game_master.calls)
        held.release.set()
        await sent_to_all([host, guest], voiced(question("Zweite?", 2)))

        assert (asked_while_voice_was_made, len(game_master.calls)) == (2, 3)

    async def test_the_final_lines_voice_follows_idle_and_keeps_a_new_start_refused_until_it_is_sent(
        self, link, game_master, voice, voices
    ):
        """GM-33: The final line's voice follows idle and a new start is refused until it is sent."""
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(*[Line(DEFAULT_QUESTION)] * (2 * QUIZ_ROUNDS), Line(FINAL_LINE))
        held = voice.hold(FINAL_LINE)
        await host.say(START)
        for number in range(1, QUIZ_ROUNDS + 1):
            await sent_to_all([host, guest], answering(host, number))
            await host.say(REVEAL)
        await sent_to_all([host, guest], IDLE)
        await host.say(START)
        await sent(host, refused(GameRefusal.ALREADY_ANSWERING))

        held.release.set()
        await voice_sent([host, guest], 2 * QUIZ_ROUNDS + 1)

        assert shown(guest)[-3:] == [line(FINAL_LINE), IDLE, {"type": VOICE_TYPE}]
        assert voices.get(voice_ids(guest)[-1]) == DEFAULT_WAV

    @pytest.mark.parametrize(
        "ending",
        [
            pytest.param(end_by_tap, id="an end"),
            pytest.param(end_by_leave, id="the starters leave"),
            pytest.param(end_by_room, id="the calls end"),
            pytest.param(end_by_shutdown, id="a shutdown"),
        ],
    )
    async def test_an_ending_during_the_voice_cancels_it_and_no_voice_is_sent(self, link, voice, ending):
        """GM-33: An ending during the voice cancels it and no voice is sent."""
        call = await call_of(link, "host", "guest")
        host, starter = call
        voice.hold(DEFAULT_QUESTION)
        await starter.say(START)
        await sent_to_all(call, answering(starter))
        [spoken] = voice.calls

        await ending(call, starter, link)
        await yield_until(lambda: spoken.finished)
        await let_every_task_run()

        assert spoken.cancelled
        assert voice_ids(host) == []


class TestAVoiceThatFails:
    @pytest.mark.parametrize("answer_of_voice", [None, RuntimeError("a voice that broke")], ids=["nothing", "a raise"])
    async def test_a_line_whose_voice_fails_leaves_the_frames_as_without_a_voice(self, link, voice, answer_of_voice):
        """GM-34: A line whose voice fails leaves the frames as without a voice."""
        host, guest = await call_of(link, "host", "guest")
        voice.answer_next(answer_of_voice)

        with structlog.testing.capture_logs() as logs:
            await host.say(START)
            await sent_to_all([host, guest], answering(host))
            await let_every_task_run()
            await guest.say(answer("Lissabon"))
            await sent_to_all([host, guest], answer_count(1, 2))

        for browser in (host, guest):
            assert shown(browser)[:3] == asked(host)
            assert voice_ids(browser) == []
        assert_no_driver_failed(logs)

    async def test_a_voice_that_raises_is_logged_by_its_type_alone(self, link, voice):
        """GM-34: A voice that raises is logged by its type alone."""
        host, guest = await call_of(link, "host", "guest")
        voice.answer_next(RuntimeError("the line's text must stay out of the log"))

        with structlog.testing.capture_logs() as logs:
            await host.say(START)
            await sent_to_all([host, guest], answering(host))
            await yield_until(lambda: [log for log in logs if log["event"] == "game_voice_raised"])

        assert [log for log in logs if log["event"] == "game_voice_raised"] == [
            {"event": "game_voice_raised", "log_level": "error", "error": "RuntimeError"}
        ]

    async def test_a_voice_past_its_deadline_is_cancelled_and_the_quiz_goes_on(
        self, link, game_master, voice, scheduler
    ):
        """GM-34: A voice past its deadline is cancelled and the quiz goes on."""
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(Line("Frage?"), Line(REVEAL_LINE), Line("Zweite?"))
        voice.hold("Frage?")
        await host.say(START)
        await sent_to_all([host, guest], answering(host))
        [spoken] = voice.calls

        with structlog.testing.capture_logs() as logs:
            await scheduler.advance(VOICE_DEADLINE_SECONDS)
            await yield_until(lambda: spoken.finished)
            await host.say(REVEAL)
            await sent_to_all([host, guest], answering(host, 2))

        assert [log for log in logs if log["event"] == "game_voice_failed"] == [
            {"event": "game_voice_failed", "log_level": "info", "outcome": "past_deadline"}
        ]
        assert spoken.cancelled
        assert shown(guest)[:6] == [*asked(host, "Frage?"), {"type": VOICE_TYPE}, thinking(host), line(REVEAL_LINE)]


class TestTheFrameThatEndsEveryVoice:
    async def test_a_line_the_server_voices_carries_the_voiced_mark(self, link, voice):
        """GM-36: A line the server will voice carries `voiced`."""
        call = await call_of(link, "host", "guest")

        await call[0].say(START)
        await voice_sent(call)

        for browser in call:
            [spoken_line] = [f for f in game_frames(browser) if f["type"] == GameSignal.GAME_LINE.value]
            assert spoken_line["voiced"] is True

    async def test_a_voice_made_ends_in_one_voice_frame_with_its_id_and_path(self, link, voice):
        """GM-36: A voice made ends in exactly one voice frame with its id and path."""
        call = await call_of(link, "host", "guest")

        await call[0].say(START)
        await voice_sent(call)
        await let_every_task_run()

        for browser in call:
            [frame] = voice_frames(browser)
            assert frame["path"].endswith(frame["id"])

    @pytest.mark.parametrize("answer_of_voice", [None, RuntimeError("a voice that broke")], ids=["nothing", "a raise"])
    async def test_a_voice_that_failed_ends_in_one_voice_frame_without_id_or_path(self, link, voice, answer_of_voice):
        """GM-36: A voice that failed ends in exactly one voice frame without id or path."""
        call = await call_of(link, "host", "guest")
        voice.answer_next(answer_of_voice)

        await call[0].say(START)
        for browser in call:
            await browser.socket.wait_until(lambda browser=browser: len(voice_frames(browser)) >= 1)
        await let_every_task_run()

        for browser in call:
            assert voice_frames(browser) == [{"type": VOICE_TYPE}]

    async def test_a_voice_past_its_deadline_ends_in_a_voice_frame_without_id(self, link, voice, scheduler):
        """GM-36: A voice past its deadline ends in a voice frame without id."""
        call = await call_of(link, "host", "guest")
        voice.hold(DEFAULT_QUESTION)
        await call[0].say(START)
        await sent_to_all(call, answering(call[0]))

        await scheduler.advance(VOICE_DEADLINE_SECONDS)
        for browser in call:
            await browser.socket.wait_until(lambda browser=browser: len(voice_frames(browser)) >= 1)

        for browser in call:
            assert voice_frames(browser) == [{"type": VOICE_TYPE}]

    async def test_the_final_voice_that_failed_follows_idle_as_an_empty_frame(self, link, game_master, voice):
        """GM-36: The final line's failed voice is an empty voice frame after idle."""
        host, guest = await call_of(link, "host", "guest")
        game_master.answer_next(*[Line(DEFAULT_QUESTION)] * (2 * QUIZ_ROUNDS), Line(FINAL_LINE))
        voice.answer_next(*[DEFAULT_WAV] * (2 * QUIZ_ROUNDS), None)
        await host.say(START)
        for number in range(1, QUIZ_ROUNDS + 1):
            await sent_to_all([host, guest], answering(host, number))
            await host.say(REVEAL)
        await sent_to_all([host, guest], IDLE)
        await let_every_task_run()

        assert shown(guest)[-3:] == [line(FINAL_LINE), IDLE, {"type": VOICE_TYPE}]
