"""What a joined agent says aloud and how every seat of its room hears it: the agent's sentences in their queue
(`speaking.py`), the windows that say it (`speech_in_windows.py`) and the frames that tell the seats the agent's sound,
which are its voices, that a person holds it, that it goes on and that its sound ends."""

from functools import partial
from typing import TYPE_CHECKING

import structlog

from hopin.participation.frames import (
    AgentContinuedFrame,
    AgentHeldFrame,
    AgentLineFrame,
    AgentStopFrame,
    AgentVoiceFrame,
)
from hopin.participation.hold_reports import HoldReports
from hopin.participation.speaking import Speaking, SpeakResult, StopResult, milliseconds
from hopin.participation.telling import send_in_room, voices_of
from hopin.participation.text import clean_line, one_line
from hopin.participation.vocabulary import HoldRefusal, SpeakFailure, SpeakStop

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from hopin.participation.frames import ParticipationFrame
    from hopin.participation.participants import DialedAgent
    from hopin.participation.places import Places
    from hopin.participation.shown_figures import ShownFigures
    from hopin.participation.speaking import EndedHold, QueuedSentence, Renderer, VoicesOffered
    from hopin.participation.speech_in_windows import SpeechInWindows
    from hopin.participation.vocabulary import FigureMood
    from hopin.participation.voice import VoiceStore
    from hopin.signaling.frames import ForeignFrame, PeerId
    from hopin.signaling.replay import Supersedes
    from hopin.signaling.scheduler import Deadlines
    from hopin.signaling.transition import FeatureTransitions, RoomView, Transition

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _log_announcement_failed(agent_id: str, error: BaseException) -> None:
    """A step of an agent's sentences that failed, such as showing a started one, by the error's type alone."""
    logger.error("agent_announcement.failed", error_type=type(error).__name__, agent_id=agent_id)


class AgentSpeech:
    """The speech of the agents of one participation. `write_line` posts a sentence whose voice failed as a chat line
    and `tell_room` sends a frame to every seat of an agent's room."""

    def __init__(
        self,
        voices: VoiceStore,
        deadlines: Deadlines,
        places: Places,
        speech: SpeechInWindows,
        figures: ShownFigures,
        write_line: Callable[[DialedAgent, str], Awaitable[None]],
        tell_room: Callable[[DialedAgent, ParticipationFrame, Supersedes | None], Awaitable[None]],
    ) -> None:
        self._speech = speech
        self._figures = figures
        self._tell_room = tell_room
        self._transitions: FeatureTransitions
        self._speaking = Speaking(
            voices,
            deadlines,
            self._sentence_started,
            speech.sentence_ended,
            write_line,
            self._hold_ended,
            self._sound_ended,
            self._in_own_turn,
        )
        self._reports = HoldReports(places, self._speaking.held_sentence)

    def attach(self, transitions: FeatureTransitions) -> None:
        self._transitions = transitions

    async def speak(
        self,
        agent: DialedAgent,
        text: str,
        render: Renderer | None,
        offered: VoicesOffered | None,
        mood: FigureMood | None,
        interrupted: str | None,
    ) -> SpeakResult:
        """Queue the agent's sentence, which plays with `mood`, behind its earlier ones once `render` made its sound,
        and answer that it plays when the sentence starts, not when it is queued; the refusal when the sentence cannot
        be spoken: no text, no renderer, past the agent's waiting queue, or an end of its speaking meanwhile; a sound
        that could not be made is written as a line instead, and `offered` then says when the voice is back, which
        turns the window's voice available without a sentence. A held agent speaks only a sentence that names its hold
        as `interrupted`. Any other speak ends the agent's thinking and its stated outcome."""
        if (hold_refusal := self._speaking.hold_refusal(agent, interrupted)) is not None:
            return hold_refusal
        await self._figures.end_turn_state(agent)
        sentence = clean_line(one_line(text))
        if sentence is None or render is None:
            return self._speech.speak_refused(
                agent, text, SpeakFailure.NO_TEXT if sentence is None else SpeakFailure.SPEAKING_OFF
            )
        if interrupted is None and self._speaking.is_full(agent):
            return self._speech.speak_refused(agent, text, SpeakFailure.QUEUE_FULL)
        return await self._speaking.speak(
            agent, sentence, self._speech.watching_voice(agent, render, offered), mood, interrupted
        )

    async def stop(self, agent: DialedAgent, interrupted: str | None) -> StopResult:
        """Cut the sentence the agent plays or holds in every seat and drop its others, which ends its hold; the cut
        sentence's text, that only others were dropped, or why nothing was cut."""
        outcome = self._speaking.stop(agent, interrupted)
        if not isinstance(outcome, SpeakStop | HoldRefusal):
            await self._stop_sound(agent)
        return outcome

    async def continue_speaking(self, agent: DialedAgent, interrupted: str) -> HoldRefusal | None:
        """End the hold `interrupted` names: the agent's held sentence goes on in every seat where it paused, then its
        queue; the refusal, or nothing when it goes on."""
        refusal = self._speaking.continue_speaking(agent, interrupted)
        if refusal is None:
            await self._tell_room(agent, AgentContinuedFrame(agent.agent_id), None)
        return refusal

    async def person_started(self, room_id: str, seat: PeerId) -> None:
        """A person starts speaking: the windows say so, each agent that hears the seat and speaks is held, and every
        seat is told all the holds in one transition, so no sound of an agent reaches a seat before its hold."""
        arisen = await self._speech.person_started(room_id, seat, self._is_speaking_or_held, self._is_held)
        if not arisen:
            return
        for agent, hold_id in arisen.items():
            self._speaking.hold(agent, hold_id)
        await self._transitions.apply(room_id, partial(self._tell_held_all, list(arisen)))

    def hold_reported(self, peer_id: PeerId, frame: ForeignFrame, view: RoomView) -> None:
        """Keep what a seat reports of the sentence an agent's hold keeps (`HoldReports.reported`)."""
        self._reports.reported(peer_id, frame, view)

    def hold_of(self, agent: DialedAgent) -> str | None:
        """The id of the hold that stands over the agent, none while it is not held."""
        return self._speaking.hold_of(agent)

    def plays(self, agent: DialedAgent) -> bool:
        """Whether a sentence of the agent plays now."""
        return self._speaking.plays(agent)

    def end(self, agent: DialedAgent) -> None:
        """The agent's removal, leave, lease end or call end, which closes its player in every seat."""
        self._speaking.end(agent)

    def park(self, agent: DialedAgent) -> None:
        """The agent's sponsor parked: its speaking ends, and the sound of a hold that stood ends in the seats that
        remain, whose player would stay paused."""
        if self._speaking.end(agent):
            self._sound_ended(agent)

    def tell_held(self, view: RoomView, transition: Transition, agent: DialedAgent, peer_id: PeerId) -> None:
        """Tell a seat that joined that the agent is held, if it is."""
        if self._is_held(agent):
            send_in_room(view, transition, [peer_id], AgentHeldFrame(agent.agent_id))

    async def _stop_sound(self, agent: DialedAgent) -> None:
        """Tell every seat that the agent's sound ends, which also ends a pause of its player."""
        await self._tell_room(agent, AgentStopFrame(agent.agent_id), voices_of(agent))

    def _sound_ended(self, agent: DialedAgent) -> None:
        """The sound of a sentence a hold kept ends in every seat, before any voice the agent's queue announces next."""
        self._in_own_turn(agent, self._stop_sound(agent))

    async def _sentence_started(self, agent: DialedAgent, sentence: QueuedSentence, at_once: bool) -> None:
        """A sentence of the agent begins to play: in one transition it enters the call's chat as the agent's line and
        every seat is told its voice, also when a hold keeps it from playing, unless the agent's end came first; a stop
        that came first leaves the line without a voice."""
        await self._transitions.apply(agent.room_id, partial(self._announce_sentence, agent, sentence, at_once))

    def _announce_sentence(
        self, agent: DialedAgent, sentence: QueuedSentence, at_once: bool, view: RoomView, transition: Transition
    ) -> None:
        if not self._speaking.speaks(agent):
            return
        standing = self._speaking.stands(agent, sentence)
        if standing and not self._is_held(agent):
            self._speech.sentence_started(agent, at_once=at_once)
        send_in_room(view, transition, view.members, AgentLineFrame(agent.agent_id, agent.name, sentence.text))
        if standing:
            voice = AgentVoiceFrame(agent.agent_id, sentence.voice.id, sentence.mood)
            send_in_room(view, transition, view.members, voice, voices_of(agent))

    def _hold_ended(self, agent: DialedAgent, hold: EndedHold) -> None:
        """The hold over the agent ended: its window follows, and its line is written with the seats' reports, which are
        then forgotten."""
        self._speech.hold_ended(agent)
        hold.timer.reason(hold.ending.value)
        if hold.played_seconds is not None:
            hold.timer.add(played_ms=milliseconds(hold.played_seconds))
        hold.timer.add(**self._reports.counts(hold.id))
        hold.timer.end()
        self._reports.clear(hold.id)

    def _tell_held_all(self, agents: list[DialedAgent], view: RoomView, transition: Transition) -> None:
        for agent in agents:
            send_in_room(view, transition, view.members, AgentHeldFrame(agent.agent_id))

    def _is_held(self, agent: DialedAgent) -> bool:
        return self._speaking.hold_of(agent) is not None

    def _is_speaking_or_held(self, agent: DialedAgent) -> bool:
        return self._speaking.is_speaking(agent) or self._is_held(agent)

    def _in_own_turn(self, agent: DialedAgent, step: Coroutine[None, None, None]) -> None:
        self._transitions.in_own_turn(step, partial(_log_announcement_failed, agent.agent_id))
