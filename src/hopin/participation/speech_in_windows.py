"""What the joined agents' windows say of speech: an agent's sentence enters them once it has been said or cut, and
people talking over a sentence of the agent stand in its window as a record until their turns end.

The record is the agents library's talked-over record. HopIn opens it for each agent that hears a person who starts
speaking while a sentence of that agent plays, updates it with the words heard, and removes it with the last person's
turn. Every change tells the agent whose window changed once.
"""

from typing import TYPE_CHECKING

import structlog

from hopin.participation.chat_window import LineKind
from hopin.participation.speaking import RenderedSentence, SentenceEnd
from hopin.participation.vocabulary import VOICE_UNAVAILABLE_EVENT, VoiceFailure

if TYPE_CHECKING:
    from collections.abc import Callable

    from hopin.participation.hearing import Hearing
    from hopin.participation.participants import DialedAgent, Person
    from hopin.participation.places import Places
    from hopin.participation.speaking import QueuedSentence, Renderer
    from hopin.signaling.features import CallRooms
    from hopin.signaling.frames import PeerId

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class SpeechInWindows:
    """Writes speech into the windows of the agents joined in a room, the seats under the names the call shows;
    `on_window_changed` tells an agent its window changed."""

    def __init__(
        self,
        places: Places,
        hearing: Hearing,
        rooms: CallRooms,
        on_window_changed: Callable[[Person, str], None],
    ) -> None:
        self._places = places
        self._hearing = hearing
        self._rooms = rooms
        self._on_window_changed = on_window_changed

    def line_said(self, room_id: str, kind: LineKind, name: str, text: str, *, sayer: Person | None) -> None:
        """Add a written line to each joined agent's window in the room and tell the agent; `sayer` is the person whose
        agent said it, whose window marks it own."""
        for person, window in self._places.windows_in(room_id).items():
            if window.append(kind, name, text, own=person == sayer):
                self._on_window_changed(person, room_id)

    def watching_voice(self, agent: DialedAgent, render: Renderer) -> Renderer:
        """The renderer, and every sound or failure of the agent's voice it makes shown in the agent's window: a
        failure turns the voice unavailable and logs that once until a sound turns it available again. A sentence that
        was refused for a reason that is no failure of the voice changes nothing."""

        async def watched(text: str, voices: tuple[str, ...]) -> RenderedSentence | VoiceFailure | None:
            rendered = await render(text, voices)
            if rendered is not None:
                self._voice_rendered(agent, rendered)
            return rendered

        return watched

    def _voice_rendered(self, agent: DialedAgent, rendered: RenderedSentence | VoiceFailure) -> None:
        window = self._places.window_of(agent.person, agent.room_id)
        if window is None:
            return
        if isinstance(rendered, VoiceFailure):
            turned = window.voice_failed()
            if turned:
                logger.info(VOICE_UNAVAILABLE_EVENT, reason=rendered.value, room_id=agent.room_id)
        else:
            turned = window.voice_worked()
        if turned:
            self._on_window_changed(agent.person, agent.room_id)

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
                LineKind.AGENT, agent.name, sentence.text, own=own, spoken=True, cut=end is SentenceEnd.CUT
            )
            if own and end is not SentenceEnd.CONTINUED:
                changed = window.set_playing(playing=False) or changed
            if changed:
                self._on_window_changed(person, agent.room_id)

    def person_said(self, room_id: str, seat: PeerId, text: str, began_at: float) -> None:
        """A sentence the seat said, which began at `began_at`, enters the window of each agent that hears the seat
        under the name the call shows, in the change that takes the seat's words out of the record."""
        heard = self._hearing.heard(room_id, seat, self._rooms.name_of(seat), text, began_at)
        if heard is None:
            return
        for person, window in self._places.windows_in(room_id).items():
            appended = person in heard.hearers and window.append(
                heard.kind, heard.name, heard.text, own=False, spoken=True
            )
            if window.turn_ended(seat) or appended:
                self._on_window_changed(person, room_id)

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
