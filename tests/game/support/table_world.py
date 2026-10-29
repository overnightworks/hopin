"""What the table tests share: the frames a quiz sends, the call they join and the waits they make."""

import asyncio
import json

from hopin.config.constants import SignalType
from hopin.game.quiz import QUIZ_ROUNDS, QUIZ_TOPICS, REVEAL_REQUEST, Speaker, Turn, question_request
from hopin.game.table import Line
from hopin.game.vocabulary import GamePhase, GameSignal
from tests.game.support.fake_game_master import DEFAULT_QUESTION
from tests.support.signaling_fakes import ARRIVAL_CAP_SECONDS, text_frame

START = {"type": GameSignal.GAME_START.value}
REVEAL = {"type": GameSignal.GAME_REVEAL.value}
END = {"type": GameSignal.GAME_END.value}
ENDED = {"type": GameSignal.GAME_ENDED.value}
END_ROOM = {"type": SignalType.END_ROOM.value}
GAME_TYPES = frozenset(
    {
        GameSignal.GAME_PHASE,
        GameSignal.GAME_LINE,
        GameSignal.GAME_REFUSED,
        GameSignal.GAME_ANSWER_COUNT,
        GameSignal.GAME_ENDED,
    }
)
IDLE = {"type": GameSignal.GAME_PHASE.value, "phase": GamePhase.IDLE.value}
TOPICS = QUIZ_TOPICS[:QUIZ_ROUNDS]
OPENING = (Turn(Speaker.HOPIN, question_request(TOPICS[0])),)


def fixed_topics():
    """The topic drawer the tests inject: every quiz gets the same topics."""
    return TOPICS


REVEAL_LINE = "Lissabon."
QUESTION_TWO = "Zweite?"
YIELD_CAP = 100


def ended_by(name):
    """The frame every other seat is sent when the starter, called `name`, leaves."""
    return {**ENDED, "leaver": name}


def answers_in_reveal(text):
    """The answers a reveal request lists: its first line is the fixed request, its second the JSON list."""
    request, answers = text.split("\n")
    assert request == REVEAL_REQUEST
    return json.loads(answers)


def script_two_questions(game_master):
    game_master.answer_next(Line("Frage?"), Line("Lissabon."), Line(QUESTION_TWO))


def revealed_answers(game_master):
    """The answers the first question's reveal run was asked to name; the second run of the game master."""
    return answers_in_reveal(game_master.calls[1].transcript[-1].text)


def quiz_phase(phase, starter, round_number=1):
    return {
        "type": GameSignal.GAME_PHASE.value,
        "phase": phase.value,
        "round": round_number,
        "rounds": QUIZ_ROUNDS,
        "starter": starter.peer_id,
    }


def thinking(starter, round_number=1):
    return quiz_phase(GamePhase.THINKING, starter, round_number)


def answering(starter, round_number=1):
    return quiz_phase(GamePhase.ANSWERING, starter, round_number)


def question(text=DEFAULT_QUESTION, round_number=1):
    return {"type": GameSignal.GAME_LINE.value, "text": text, "round": round_number}


def line(text):
    return {"type": GameSignal.GAME_LINE.value, "text": text}


def revealed_into_question_two(starter):
    """What every seat sees after the reveal, whoever tapped it: the line, then question 2 with answering."""
    return [
        thinking(starter),
        line("Lissabon."),
        thinking(starter, 2),
        question(QUESTION_TWO, 2),
        answering(starter, 2),
    ]


def asked(starter, text=DEFAULT_QUESTION):
    """What every seat sees of a question the starter's tap asked."""
    return [thinking(starter), question(text), answering(starter)]


def answer(text):
    return {"type": GameSignal.GAME_ANSWER.value, "text": text}


def answer_count(answered, players):
    return {"type": GameSignal.GAME_ANSWER_COUNT.value, "answered": answered, "players": players}


def refused(reason, **fields):
    return {"type": GameSignal.GAME_REFUSED.value, "reason": reason.value, **fields}


def without_seq(frame):
    return {key: value for key, value in frame.items() if key != "seq"}


def game_frames(browser):
    """The game frames the browser was sent, over all its sockets, without their sequence numbers."""
    return [without_seq(frame) for frame in browser.frames(*GAME_TYPES)]


def every_frame_sent_to(browser):
    """Every frame the browser was sent over all its sockets, of any type, core errors included."""
    return [frame for socket in browser.sockets for frame in socket.sent]


def sequenced_game_frames(browser):
    return [(frame["seq"], without_seq(frame)) for frame in browser.frames(*GAME_TYPES)]


async def sent(browser, frame, times=1):
    """Wait until the browser has been sent the frame `times` times."""
    await browser.socket.wait_until(lambda: game_frames(browser).count(frame) >= times)


async def sent_to_all(browsers, frame, times=1):
    for browser in browsers:
        await sent(browser, frame, times)


async def wait_within_cap(event):
    async with asyncio.timeout(ARRIVAL_CAP_SECONDS):
        await event.wait()


async def finished_within_cap(task):
    async with asyncio.timeout(ARRIVAL_CAP_SECONDS):
        await task


async def shut_down_within_cap(handler):
    """Shut down, failing instead of hanging when a run's ask is never cancelled."""
    async with asyncio.timeout(ARRIVAL_CAP_SECONDS):
        await handler.shut_down()


async def yield_until(condition):
    """Let the other tasks run until the condition holds; for a step that no frame announces."""
    for _ in range(YIELD_CAP):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("the condition never held")


async def let_every_task_run():
    """Let the other tasks run to their next wait; for the absence of a step that no frame announces."""
    for _ in range(YIELD_CAP):
        await asyncio.sleep(0)


async def call_of(link, *names):
    host = await link.join(names[0])
    room_id = host.socket.sent[0]["room_id"]
    return [host, *[await link.join(name, room_id) for name in names[1:]]]


async def start_and_hold(game_master, browser, **hold_options):
    held = game_master.hold_next(**hold_options)
    await browser.say(START)
    await wait_within_cap(held.started)
    return held


async def open_a_question(call, starter):
    """The starter's tap, until every seat of the call has the question open."""
    await starter.say(START)
    await sent_to_all(call, answering(starter))


async def broken_write(_text):
    raise RuntimeError("a write that broke")


def hold_writes_from(browser, frame, entered=None):
    """Hold the browser's writes from the frame on until the returned gate opens.

    `entered`, when given, is set as the held frame's write begins.
    """
    gate = asyncio.Event()
    send_text = browser.socket.send_text

    async def send_text_once_open(text):
        if without_seq(json.loads(text)) == frame:
            browser.socket.send_gate = gate
            if entered is not None:
                entered.set()
        await send_text(text)

    browser.socket.send_text = send_text_once_open
    return gate


def push_unsettled(browser, data):
    """Hand the handler the browser's next frame without waiting for the handler to go idle."""
    browser.sent_count += 1
    browser.socket.push(text_frame(data, seq=browser.sent_count))


def assert_no_driver_failed(logs):
    assert [log for log in logs if log["event"] == "game_driver_failed"] == []


def assert_no_game_frame_after_room_closed(browsers):
    for browser in browsers:
        frames = [frame for socket in browser.sockets for frame in socket.sent]
        [closed] = [frame["seq"] for frame in frames if frame["type"] == SignalType.ROOM_CLOSED.value]
        assert [frame for frame in frames if frame["type"] in GAME_TYPES and frame["seq"] > closed] == []
