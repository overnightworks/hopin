"""What the table tests share: the frames a quiz sends, the call they join and the waits they make."""

import asyncio
import json

from hopin.config.constants import SignalType
from hopin.game.quiz import NEXT_QUESTION_REQUEST, QUIZ_ROUNDS, Speaker, Turn
from hopin.game.vocabulary import GamePhase, GameSignal
from tests.game.support.fake_game_master import DEFAULT_QUESTION
from tests.support.signaling_fakes import ARRIVAL_CAP_SECONDS

START = {"type": GameSignal.GAME_START.value}
REVEAL = {"type": GameSignal.GAME_REVEAL.value}
END_ROOM = {"type": SignalType.END_ROOM.value}
GAME_TYPES = frozenset({GameSignal.GAME_PHASE, GameSignal.GAME_LINE, GameSignal.GAME_REFUSED})
IDLE = {"type": GameSignal.GAME_PHASE.value, "phase": GamePhase.IDLE.value}
OPENING = (Turn(Speaker.HOPIN, NEXT_QUESTION_REQUEST),)
REVEAL_LINE = "Lissabon."
YIELD_CAP = 100


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


def asked(starter, text=DEFAULT_QUESTION):
    """What every seat sees of a question the starter's tap asked."""
    return [thinking(starter), question(text), answering(starter)]


def refused(reason, **fields):
    return {"type": GameSignal.GAME_REFUSED.value, "reason": reason.value, **fields}


def without_seq(frame):
    return {key: value for key, value in frame.items() if key != "seq"}


def game_frames(browser):
    """The game frames the browser was sent, over all its sockets, without their sequence numbers."""
    return [without_seq(frame) for frame in browser.frames(*GAME_TYPES)]


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


def hold_writes_from(browser, frame):
    """Hold the browser's writes from the frame on until the returned gate opens."""
    gate = asyncio.Event()
    send_text = browser.socket.send_text

    async def send_text_once_open(text):
        if without_seq(json.loads(text)) == frame:
            browser.socket.send_gate = gate
        await send_text(text)

    browser.socket.send_text = send_text_once_open
    return gate
