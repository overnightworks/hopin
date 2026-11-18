"""What the joined agents' windows say of speech: an agent's sentence enters them once it has been said or cut, and
people talking over a sentence of the agent stand in its window as a record until their turns end.

The record is the agents library's talked-over record. HopIn opens it for each agent that hears a person who starts
speaking while a sentence of that agent plays, updates it with the words heard, and removes it with the last person's
turn. Every change tells the agent whose window changed once.

While a window states its agent's voice unavailable, the speech service is asked at a fixed interval whether a voice of
the agent is offered again, and the window turns available once it is, with no sentence needed; with no such window
nothing is asked.
"""

from contextlib import AbstractContextManager, contextmanager, nullcontext
from enum import StrEnum
from typing import TYPE_CHECKING, Final, NamedTuple
from weakref import WeakKeyDictionary

import structlog
from logline import current_traceparent, unit_trace

from hopin.participation.chat_window import LineKind
from hopin.participation.participants import DialedAgent
from hopin.participation.speaking import RenderedSentence, SentenceEnd, speak_timer
from hopin.participation.vocabulary import NO_VOICE_REASON, VOICE_RECOVERY_SECONDS, VoiceFailure, VoiceRefusedError
from hopin.rooms.reference import room_reference
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from logline import StepTimer

    from hopin.participation.chat_window import ChatWindow
    from hopin.participation.hearing import Hearing
    from hopin.participation.participants import Person
    from hopin.participation.places import Places
    from hopin.participation.speaking import QueuedSentence, Renderer, VoicesOffered
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import PeerId
    from hopin.signaling.scheduler import Deadlines

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

WINDOW_RECOVERY_KEY: Final = "voices"


class EntryKind(StrEnum):
    """How a line reached a window: a sentence a person said aloud, or a line someone wrote."""

    HEARD = "heard"
    WRITTEN = "written"


class _PulsedTurn(NamedTuple):
    """The trace of a heard sentence's turn and the `seq` of the line it entered, which the agent's pulse told."""

    seq: int
    traceparent: str


def _note_rendering(step: StepTimer, rendered: RenderedSentence | VoiceFailure | None) -> None:
    """End the speak step for what the render made: its first sound, the failure of the voice, or no voice to ask."""
    if rendered is None:
        step.refuse(NO_VOICE_REASON)
    elif isinstance(rendered, VoiceFailure):
        step.refuse(rendered.value)
    else:
        step.first_output()


class SpeechInWindows:
    """Writes speech into the windows of the agents joined in a room, the seats under the names the call shows;
    `on_window_changed` tells an agent its window changed."""

    def __init__(
        self,
        places: Places,
        hearing: Hearing,
        rooms: CallRooms,
        on_window_changed: Callable[[Person, str], None],
        deadlines: Deadlines,
    ) -> None:
        self._places = places
        self._hearing = hearing
        self._rooms = rooms
        self._on_window_changed = on_window_changed
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.WINDOW_RECOVERY, self._ask_whether_voices_are_back)
        self._recovery_offered: VoicesOffered | None = None
        self._turn_traces: WeakKeyDictionary[ChatWindow, _PulsedTurn] = WeakKeyDictionary()

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
        """The `speak.ended` timer of a sentence the agent's speaking asked for, with the `seq` its window has now. Its
        line is written in the trace of the heard sentence the window's newest line is, so the sentence joins the turn
        that caused it; once another line has entered the window, or with no heard sentence, it stays in the trace of
        the call that carries the sentence."""
        window = self._places.window_of(agent.person, agent.room_id)
        turn_trace = None if window is None else self._newest_line_trace(window)
        in_turn: AbstractContextManager[None] = nullcontext() if turn_trace is None else unit_trace(turn_trace)
        with in_turn, speak_timer(agent, text, seq=None if window is None else window.last_seq) as step:
            yield step

    def _newest_line_trace(self, window: ChatWindow) -> str | None:
        pulsed = self._turn_traces.get(window)
        return pulsed.traceparent if pulsed is not None and pulsed.seq == window.last_seq else None

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

    def sentence_started(self, agent: DialedAgent) -> None:
        """A sentence of the agent plays: the record that stands says so."""
        window = self._places.window_of(agent.person, agent.room_id)
        if window is not None and window.set_playing(playing=True):
            self._on_window_changed(agent.person, agent.room_id)

    def sentence_ended(self, agent: DialedAgent, sentence: QueuedSentence, end: SentenceEnd) -> None:
        """A sentence of the agent was said to its end or cut: it enters every window in the room as a spoken line,
        the agent's own as its own, and the agent's record says it plays no more unless its next sentence starts at
        once, all in one change of each window."""
        for person, window in self._places.windows_in(agent.room_id).items():
            own = person == agent.person
            changed = window.append(
                LineKind.AGENT,
                agent.name,
                sentence.text,
                own=own,
                spoken=True,
                cut=end is SentenceEnd.CUT,
                traceparent=current_traceparent(),
            )
            if own and end is not SentenceEnd.CONTINUED:
                changed = window.set_playing(playing=False) or changed
            if changed:
                self._on_window_changed(person, agent.room_id)

    def person_said(self, room_id: str, seat: PeerId, text: str, began_at: float, item_id: str) -> None:
        """A sentence the seat said, which began at `began_at` and which the speech service knows as `item_id`, enters
        the window of each agent that hears the seat under the name the call shows, in the change that takes the seat's
        words out of the record."""
        heard = self._hearing.heard(room_id, seat, self._rooms.name_of(seat), text, began_at)
        if heard is None:
            return
        for person, window in self._places.windows_in(room_id).items():
            appended = person in heard.hearers and window.append(
                heard.kind, heard.name, heard.text, own=False, spoken=True, traceparent=current_traceparent()
            )
            turn_ended = window.turn_ended(seat)
            if appended:
                self._line_entered(person, room_id, window, EntryKind.HEARD, item_id)
            if turn_ended or appended:
                self._on_window_changed(person, room_id)
            if appended:
                self._pulse_sent(person, room_id, window)
                self._remember_turn(window)

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

    def _remember_turn(self, window: ChatWindow) -> None:
        turn_trace = current_traceparent()
        if turn_trace is not None:
            self._turn_traces[window] = _PulsedTurn(window.last_seq, turn_trace)

    def _agent_id_of(self, person: Person, room_id: str) -> str | None:
        agent = self._places.get(person, room_id)
        return agent.agent_id if isinstance(agent, DialedAgent) else None

    def person_started(self, room_id: str, seat: PeerId) -> None:
        """A person starts speaking: each agent that hears the seat is talked over while one of its sentences plays."""
        for person in self._hearing.hearers(room_id, seat):
            window = self._places.window_of(person, room_id)
            if window is not None and window.talk_over(seat, self._rooms.name_of(seat)):
                self._on_window_changed(person, room_id)

    def words_heard(self, room_id: str, seat: PeerId, text: str) -> None:
        """The words the seat has said so far in its turn."""
        for person, window in self._places.windows_in(room_id).items():
            if window.hear_partial(seat, text):
                self._on_window_changed(person, room_id)

    def hearing_switched(self, agent: DialedAgent, *, on: bool) -> None:
        """The sponsor switched the agent's hearing: when off, the sponsor's words leave the agent's window at once."""
        window = self._places.window_of(agent.person, agent.room_id)
        if not on and window is not None and window.turn_ended(agent.sponsor):
            self._on_window_changed(agent.person, agent.room_id)

    def turn_ended(self, room_id: str, seat: PeerId) -> None:
        """The seat's turn ended, or it can be heard no more: its words leave every window in the room."""
        for person, window in self._places.windows_in(room_id).items():
            if window.turn_ended(seat):
                self._on_window_changed(person, room_id)
