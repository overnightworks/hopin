"""The call of three people with a recording player on each page and an agent that speaks through a speech origin,
and the HopIn that serves it."""

import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from agents.member import STOP_SPEAKING_TOOL
from playwright.sync_api import Page, expect
from speech_client.wire import SPEECH_PATH, RefusalCode, VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import LEAVE_CALL
from tests.e2e.support.dialed_agent import AGENT_TILE, REQUEST_SECONDS, OwnAccountAgent, call_place, room_of_link
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.support.speech_origin import (
    AUDIENCE_SCOPE,
    chunked_wav,
    chunked_wav_in_two,
    refusal_answer,
    silent_wav,
    speaking,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from concurrent.futures import Future

    import pytest
    from agents.member import PlaceWindow, SpeakAnswer, SpeakRefusal

    from tests.e2e.support.participation_scene import Scene
    from tests.support.loopback_origin import Origin

SPEAKING_TILE = re.compile(r"\bspeaking\b")

# Each page records the length of every sound its players start, how many sources it stopped and how often a player
# suspended and resumed its context, which is what a pause and its resume do. A sound has ended when the source that
# ended was the last chunk its context started, an earlier chunk ending being the sound still going on.
# A player starts a sound as the chunks of its body arrive, each chunk beginning where the one before it ends on its
# own context's clock, so the chunks of one context that join up are one sound,
# whichever other player starts between them.
RECORD_PLAYBACK = """
() => {
    window.soundsStarted = [];
    window.soundsStopped = 0;
    window.sourcesStarted = 0;
    window.sourcesEnded = 0;
    window.soundsEnded = 0;
    window.contextsSuspended = 0;
    window.contextsResumed = 0;
    const lastSoundOf = new WeakMap();
    const start = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (when, ...args) {
        const last = lastSoundOf.get(this.context);
        let index = last?.index;
        if (last?.joinsAt === when) window.soundsStarted[index] += this.buffer.duration;
        else index = window.soundsStarted.push(this.buffer.duration) - 1;
        const joinsAt = when + this.buffer.duration;
        lastSoundOf.set(this.context, { index, joinsAt });
        window.sourcesStarted += 1;
        this.addEventListener('ended', () => {
            window.sourcesEnded += 1;
            if (lastSoundOf.get(this.context).joinsAt === joinsAt) window.soundsEnded += 1;
        });
        return start.call(this, when, ...args);
    };
    const stop = AudioBufferSourceNode.prototype.stop;
    AudioBufferSourceNode.prototype.stop = function (...args) {
        window.soundsStopped += 1;
        return stop.apply(this, args);
    };
    for (const [name, counter] of [['suspend', 'contextsSuspended'], ['resume', 'contextsResumed']]) {
        const original = AudioContext.prototype[name];
        AudioContext.prototype[name] = function (...args) {
            window[counter] += 1;
            return original.apply(this, args);
        };
    }
}
"""


@contextmanager
def speaking_hopin(
    tmp_path_factory: pytest.TempPathFactory, name: str, speech_base_url: str, *, hears: bool = False
) -> Iterator[RunningServer]:
    """HopIn with participation and the setting that lets agents speak on, speaking through the speech service at
    `speech_base_url`, and hearing too when `hears`; its directories are made under `name`."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp(f"{name}-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp(f"{name}-tls"),
        participation=True,
        hears=hears,
        speaks=True,
        speech=SpeechService(speech_base_url, VoiceId.THORSTEN, AUDIENCE_SCOPE),
    ) as server:
        yield server


class Speaker:
    """The call of three people whose member added the owner's agent, a recording player on each page, and the agent's
    sentences."""

    def __init__(
        self,
        scene: Scene,
        speech_origin: Origin,
        *,
        other: OwnAccountAgent | None = None,
        voices: Sequence[str] | None = None,
        figure: dict[str, str] | None = None,
    ) -> None:
        self.scene = scene
        self.speech_origin = speech_origin
        self.room = room_of_link(scene.link)
        scene.member_adds_and_the_agent_accepts(figure, voices)
        if other is not None:
            scene.member_adds_and_accepts(other)
        for person in scene.everyone:
            person.evaluate(RECORD_PLAYBACK)
            expect(person.locator(AGENT_TILE)).to_have_count(1 if other is None else 2)

    def speak(self, seconds: float, text: str = "Guten Tag", agent: OwnAccountAgent | None = None) -> None:
        """Say a sentence of an agent that is silent and return once it started; behind a playing sentence the wait
        would hold the page routes, so `speak_behind` serves that."""
        self.speak_behind(seconds, text, agent).result(REQUEST_SECONDS)

    def speak_behind(
        self, seconds: float, text: str = "Guten Tag", agent: OwnAccountAgent | None = None
    ) -> Future[SpeakAnswer | SpeakRefusal]:
        """Say a sentence and return at once with its answer to come when it starts, behind the ones before it."""
        speaking(self.speech_origin, chunked_wav(silent_wav(seconds)))
        return (self.scene.agent if agent is None else agent).speak_unawaited(self.room, text)

    def speak_while_speech_pauses(self, seconds: float, text: str = "Guten Tag") -> Future[SpeakAnswer | SpeakRefusal]:
        """Say a sentence whose second half speech renders only once `finish_rendering` says so."""
        return self._speak_with_second_half(seconds, text, lambda rest: rest)

    def speak_while_speech_breaks(self, seconds: float, text: str = "Guten Tag") -> Future[SpeakAnswer | SpeakRefusal]:
        """Say a sentence whose stream speech breaks in its second half once `finish_rendering` says so."""
        return self._speak_with_second_half(seconds, text, lambda rest: rest[: len(rest) // 2])

    def _speak_with_second_half(
        self, seconds: float, text: str, later_body: Callable[[bytes], bytes]
    ) -> Future[SpeakAnswer | SpeakRefusal]:
        first, rest = chunked_wav_in_two(silent_wav(seconds))
        self.speech_origin.serve(SPEECH_PATH, first, later=later_body(rest))
        return self.scene.agent.speak_unawaited(self.room, text)

    def speech_was_asked(self) -> bool:
        """Whether the server asked speech to render the latest `speak_behind` sentence, so that a stop sent now
        cannot overtake it."""
        return self.speech_origin.received[SPEECH_PATH].wait(REQUEST_SECONDS)

    def speech_has_rendered_the_rest(self) -> bool:
        return self.speech_origin.resumed[SPEECH_PATH].is_set()

    def finish_rendering(self) -> None:
        self.speech_origin.resume[SPEECH_PATH].set()

    def speak_with_a_failing_voice(self, text: str) -> SpeakAnswer | SpeakRefusal:
        """Say a sentence while the speech service is busy; it answers at once, for the sentence is written."""
        speaking(self.speech_origin, refusal_answer(RefusalCode.BUSY, retry_after_seconds=3))
        return self.scene.agent.speak(self.room, text)

    def window(self) -> PlaceWindow:
        return self.scene.agent.window(self.room)

    def stop(self) -> dict[str, Any]:
        return self.scene.agent.call(STOP_SPEAKING_TOOL, {"place": call_place(self.room).model_dump()})

    def leave(self) -> dict[str, Any]:
        return self.scene.agent.call(LEAVE_CALL, {"room": self.room})

    def refusal_of_a_second_leave(self) -> str:
        return self.scene.agent.refusal_of(LEAVE_CALL, {"room": self.room})

    def started(self, person: Page) -> list[float]:
        return [round(seconds, 2) for seconds in person.evaluate("() => window.soundsStarted")]
