"""What the joined agents' windows say of speech: an agent's sentence enters them once it has been said or cut, and
people talking over a sentence of the agent stand in its window as a record until their turns end.

The record is the agents library's talked-over record. HopIn opens it for each agent that hears a person who starts
speaking while a sentence of that agent plays or waits, or while it is held, updates it with the words heard, and
removes it with the last person's turn. The agent's speaking owns the hold and whether a sentence plays, and a read of
the window takes both from it; the turns of the people the agent hears reach its speaking through one callback, which
says whether a hold arose. Every change tells the agent whose window changed once.

A line enters each agent's window as `line.entered` (room reference, agent id, the window's `seq`, and `kind`: `heard`
for the sound of a sentence, `written` for a chat line or an agent's written line), and the agent is handed the change
as `pulse.sent`, which ends at the hand-off to the agents library's listen stream: the library writes the pulse to the
stream and owns that time. A heard line carries the `item_id` of the transcript that speech sent, and a written one
none. Each line enters with the `traceparent` current at that moment, the session's outside a turn, and the agent
reads it on the line's `PlaceEntry`, so the turn the line wakes continues its trace. A voice that was lost and is
available again writes `agent_voice.returned` once per return, as `agent_voice.lost` writes the loss.

While a window states its agent's voice unavailable, the speech service is asked at a fixed interval whether a voice of
the agent is offered again, and the window turns available once it is, with no sentence needed; with no such window
nothing is asked.
"""

from contextlib import contextmanager
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import structlog
from logline import current_traceparent

from hopin.participation.chat_window import LineKind
from hopin.participation.participants import DialedAgent
from hopin.participation.speaking import RenderedSentence, SentenceEnd, Turn, speak_timer
from hopin.participation.vocabulary import (
    NO_VOICE_REASON,
    VOICE_RECOVERY_SECONDS,
    AgentRefusal,
    SpeakFailure,
    VoiceFailure,
    VoiceRefusedError,
)
from hopin.rooms.reference import room_reference
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from logline import StepTimer

    from hopin.participation.chat_window import ChatWindow
    from hopin.participation.hearing import Hearing
    from hopin.participation.participants import Person
    from hopin.participation.places import Places
    from hopin.participation.speaking import QueuedSentence, Renderer, VoicesOffered
    from hopin.signaling.frames import PeerId
    from hopin.signaling.scheduler import Deadlines

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

WINDOW_RECOVERY_KEY: Final = "voices"


type TurnHeard = Callable[[DialedAgent, PeerId, Turn, str], bool]
"""Tells an agent's speaking what a person's turn did, its words or finished line as the text; whether a hold arose."""


class EntryKind(StrEnum):
    """How a line reached a window: a sentence a person said aloud, or a line someone wrote."""

    HEARD = "heard"
    WRITTEN = "written"


def _note_rendering(step: StepTimer, rendered: RenderedSentence | VoiceFailure | None) -> None:
    """End the speak step for what the render made: its first sound, the failure of the voice, or no voice to ask."""
    if rendered is None:
        step.refuse(NO_VOICE_REASON)
    elif isinstance(rendered, VoiceFailure):
        step.refuse(rendered.value)
    else:
        step.first_output()


class SpeechInWindows:
    """Writes speech into the windows of the agents joined in a room, the seats under the names the call shows, which
    `name_of` reads; `on_window_changed` tells an agent its window changed."""

    def __init__(
        self,
        places: Places,
        hearing: Hearing,
        name_of: Callable[[PeerId], Awaitable[str]],
        on_window_changed: Callable[[Person, str], None],
        deadlines: Deadlines,
    ) -> None:
        self._places = places
        self._hearing = hearing
        self._name_of = name_of
        self._on_window_changed = on_window_changed
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.WINDOW_RECOVERY, self._ask_whether_voices_are_back)
        self._recovery_offered: VoicesOffered | None = None
        self._turn: TurnHeard = lambda *_: False  # no hold arises until an agent's speaking hears the turns

    def hear_turns_through(self, turn: TurnHeard) -> None:
        """Hand the turns of the people an agent hears to `turn`, the agent's speaking, which says whether a hold arose
        with a turn's beginning."""
        self._turn = turn

    def line_said(self, room_id: str, kind: LineKind, name: str, text: str, *, sayer: Person | None) -> None:
        """Add a written line to each joined agent's window in the room and tell the agent; `sayer` is the person whose
        agent said it, whose window marks it own."""
        for person, window in self._places.windows_in(room_id).items():
            if window.append(kind, name, text, own=person == sayer, traceparent=current_traceparent()):
                self._line_entered(person, room_id, window, EntryKind.WRITTEN)
                self._on_window_changed(person, room_id)
                self._pulse_sent(person, room_id, window)

    @contextmanager
    def speak_step(self, agent: DialedAgent, text: str) -> Iterator[StepTimer]:
        """The `speak.ended` timer of a sentence the agent's speaking asked for, with the `seq` its window has now; its
        line is written in the trace of the call that carries the sentence."""
        window = self._places.window_of(agent.person, agent.room_id)
        with speak_timer(agent, text, seq=None if window is None else window.last_seq) as step:
            yield step

    def watching_voice(self, agent: DialedAgent, render: Renderer, offered: VoicesOffered | None) -> Renderer:
        """The renderer, and every sound or failure of the agent's voice it makes shown in the agent's window: a
        failure turns the voice unavailable and logs that once until a sound, or `offered` saying that a voice of the
        agent is back, turns it available again. A sentence that was refused for a reason that is no failure of the
        voice changes nothing."""

        async def watched(text: str, voices: tuple[str, ...]) -> RenderedSentence | VoiceFailure | None:
            rendered: RenderedSentence | VoiceFailure | None = None
            refused: VoiceRefusedError | None = None
            with self.speak_step(agent, text) as step:
                try:
                    rendered = await render(text, voices)
                except VoiceRefusedError as refusal:
                    refused = refusal
                    step.refuse(refusal.reason)
                else:
                    _note_rendering(step, rendered)
            if refused is not None:
                raise refused
            if rendered is not None:
                self._voice_rendered(agent, rendered, offered)
            return rendered

        return watched

    def speak_refused(self, agent: DialedAgent, text: str, failure: SpeakFailure) -> AgentRefusal:
        """Answer `unavailable` for a sentence never rendered, its closed cause on its speak line and nothing else."""
        with self.speak_step(agent, text) as step:
            step.refuse(failure.value)
        return AgentRefusal.UNAVAILABLE

    def stop_recovery(self) -> None:
        """Stop asking whether a voice is back."""
        self._deadlines.cancel(DeadlineKind.WINDOW_RECOVERY, WINDOW_RECOVERY_KEY)
        self._recovery_offered = None

    def _voice_rendered(
        self, agent: DialedAgent, rendered: RenderedSentence | VoiceFailure, offered: VoicesOffered | None
    ) -> None:
        window = self._places.window_of(agent.person, agent.room_id)
        if window is None:
            return
        if isinstance(rendered, VoiceFailure):
            turned = window.voice_failed()
            if turned:
                logger.info(
                    "agent_voice.lost",
                    reason=rendered.value,
                    room_ref=room_reference(agent.room_id),
                    agent_id=agent.agent_id,
                )
            if offered is not None:
                self._ask_again_later(offered)
        else:
            turned = window.voice_worked()
            if turned:
                self._voice_returned(agent)
        if turned:
            self._on_window_changed(agent.person, agent.room_id)

    @staticmethod
    def _voice_returned(agent: DialedAgent) -> None:
        logger.info("agent_voice.returned", room_ref=room_reference(agent.room_id), agent_id=agent.agent_id)

    def _ask_again_later(self, offered: VoicesOffered) -> None:
        if self._recovery_offered is None:
            self._recovery_offered = offered
            self._deadlines.set(DeadlineKind.WINDOW_RECOVERY, WINDOW_RECOVERY_KEY, VOICE_RECOVERY_SECONDS)

    async def _ask_whether_voices_are_back(self, key: str) -> None:  # NOSONAR(S7503) the deadlines await it
        del key
        offered = self._recovery_offered
        self._recovery_offered = None
        assert offered is not None  # noqa: S101 - the offer is set with its deadline and cleared by cancelling it
        for agent in self._places.joined():
            window = self._places.window_of(agent.person, agent.room_id)
            if window and window.voice_unavailable and await offered(agent.voices) and window.voice_worked():
                self._voice_returned(agent)
                self._on_window_changed(agent.person, agent.room_id)
        if any(window.voice_unavailable for window in self._windows()):
            self._ask_again_later(offered)

    def _windows(self) -> list[ChatWindow]:
        windows = (self._places.window_of(agent.person, agent.room_id) for agent in self._places.joined())
        return [window for window in windows if window is not None]

    def sentence_started(self, agent: DialedAgent, *, at_once: bool) -> None:
        """A sentence of the agent plays: the record that stands says so, unless the sentence starts at once after one
        that was continued into it, which kept the record saying a sentence plays."""
        window = self._places.window_of(agent.person, agent.room_id)
        if window is not None and window.has_record and not at_once:
            self._on_window_changed(agent.person, agent.room_id)

    def sentence_ended(self, agent: DialedAgent, sentence: QueuedSentence, end: SentenceEnd) -> None:
        """A sentence of the agent was said to its end or cut: it enters every window in the room as a spoken line, the
        agent's own as its own, in one change of each window."""
        for person, window in self._places.windows_in(agent.room_id).items():
            changed = window.append(
                LineKind.AGENT,
                agent.name,
                sentence.text,
                own=person == agent.person,
                spoken=True,
                cut=end is SentenceEnd.CUT,
                continues=end is SentenceEnd.CONTINUED,
                traceparent=current_traceparent(),
            )
            if changed:
                self._on_window_changed(person, agent.room_id)

    async def person_said(self, room_id: str, seat: PeerId, text: str, began_at: float, item_id: str) -> None:
        """A sentence the seat said, which began at `began_at` and which the speech service knows as `item_id`, enters
        the window of each agent that hears the seat under the name the call shows, in the change that takes the seat's
        words out of the record."""
        heard = self._hearing.heard(room_id, seat, await self._name_of(seat), text, began_at)
        if heard is None:
            return
        for person, window in self._places.windows_in(room_id).items():
            hears = person in heard.hearers
            if hears:
                self._turn(self._agent_of(person, room_id), seat, Turn.LINE, heard.text)
            appended = hears and window.append(
                heard.kind, heard.name, heard.text, own=False, spoken=True, traceparent=current_traceparent()
            )
            turn_ended = window.turn_ended(seat)
            if appended:
                self._line_entered(person, room_id, window, EntryKind.HEARD, item_id)
            if turn_ended or appended:
                self._on_window_changed(person, room_id)
            if appended:
                self._pulse_sent(person, room_id, window)

    def _line_entered(
        self, person: Person, room_id: str, window: ChatWindow, kind: EntryKind, item_id: str | None = None
    ) -> None:
        logger.info(
            "line.entered",
            room_ref=room_reference(room_id),
            agent_id=self._agent_id_of(person, room_id),
            seq=window.last_seq,
            kind=kind,
            item_id=item_id,
        )

    def _pulse_sent(self, person: Person, room_id: str, window: ChatWindow) -> None:
        logger.info("pulse.sent", agent_id=self._agent_id_of(person, room_id), seq=window.last_seq)

    def _agent_id_of(self, person: Person, room_id: str) -> str | None:
        agent = self._places.get(person, room_id)
        return agent.agent_id if isinstance(agent, DialedAgent) else None

    async def person_started(
        self, room_id: str, seat: PeerId, opens: Callable[[DialedAgent], bool]
    ) -> list[DialedAgent]:
        """A person starts speaking: each agent that hears the seat is talked over, a new record opening only when
        `opens` says a sentence of the agent plays or waits or it is held, and told the turn began when the seat joins
        the record. A turn the record already holds begins no second one. The agents a hold arose over."""
        arisen: list[DialedAgent] = []
        for person in self._hearing.hearers(room_id, seat):
            window = self._places.window_of(person, room_id)
            assert window is not None  # noqa: S101 - only a joined agent hears
            agent = self._agent_of(person, room_id)
            name = await self._name_of(seat)
            added = window.talk_over(seat, name, opens=opens(agent))
            if added:
                self._on_window_changed(person, room_id)
            if added and self._turn(agent, seat, Turn.BEGAN, ""):
                arisen.append(agent)
        return arisen

    def hold_ended(self, agent: DialedAgent) -> None:
        """The hold over the agent ended: its window changed, which now says that it is not held and whether the held
        sentence plays on."""
        if self._places.window_of(agent.person, agent.room_id) is not None:
            self._on_window_changed(agent.person, agent.room_id)

    def words_heard(self, room_id: str, seat: PeerId, text: str) -> None:
        """The words the seat has said so far in its turn."""
        hearers = self._hearing.hearers(room_id, seat)
        for person, window in self._places.windows_in(room_id).items():
            if person in hearers:
                self._turn(self._agent_of(person, room_id), seat, Turn.WORDS, text)
            if window.hear_partial(seat, text):
                self._on_window_changed(person, room_id)

    def hearing_switched(self, agent: DialedAgent, *, on: bool) -> None:
        """The sponsor switched the agent's hearing: when off, the sponsor's turn ends for the agent and its words leave
        its window at once."""
        if on:
            return
        self._turn(agent, agent.sponsor, Turn.ENDED, "")
        window = self._places.window_of(agent.person, agent.room_id)
        if window is not None and window.turn_ended(agent.sponsor):
            self._on_window_changed(agent.person, agent.room_id)

    def turn_ended(self, room_id: str, seat: PeerId) -> None:
        """The seat's turn ended, or it can be heard no more: its words leave every window in the room."""
        for person, window in self._places.windows_in(room_id).items():
            self._turn(self._agent_of(person, room_id), seat, Turn.ENDED, "")
            if window.turn_ended(seat):
                self._on_window_changed(person, room_id)

    def _agent_of(self, person: Person, room_id: str) -> DialedAgent:
        agent = self._places.get(person, room_id)
        assert isinstance(agent, DialedAgent)  # noqa: S101 - only a joined agent holds a window
        return agent
