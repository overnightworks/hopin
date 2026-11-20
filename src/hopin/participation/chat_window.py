"""The window of a call's chat one joined agent reads: the lines said in its room since it joined, bounded, the
record of people talking over the agent's sentence and the ids of the holds that record opens; whether the agent is
held and plays is its speaking's state, which a read takes from its caller.

Plain state with no clock and no I/O. It lives and dies with the agent's place and keeps no history beyond its
bounds.
"""

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

LINES_PER_WINDOW: Final = 100
TEXT_BYTES_PER_WINDOW: Final = 256 * 1024


class LineKind(StrEnum):
    """Who said a line: a person in the call or an agent in it."""

    PERSON = "person"
    AGENT = "agent"


class WindowVoice(StrEnum):
    """Whether the agent's voice at the place works: it fails for the app's own reasons only, never for a social one."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class WindowLine:
    """One line of a window; `traceparent` is the trace it entered in, none when it entered outside a trace."""

    seq: int
    kind: LineKind
    name: str
    text: str
    own: bool
    spoken: bool = False
    cut: bool = False
    continues: bool = False
    traceparent: str | None = None


@dataclass(frozen=True)
class WindowPartial:
    """What one person has said so far in a turn that has not ended; `text` is empty until a word is heard."""

    name: str
    text: str


@dataclass(frozen=True)
class WindowTalkedOver:
    """People talking over the agent: `playing` says whether a sentence of the agent plays now."""

    id: str
    playing: bool
    speech: tuple[WindowPartial, ...]


@dataclass(frozen=True)
class WindowRead:
    """The lines a window holds, oldest first. `starts_at` is the `seq` of the first, or of the next line when there
    is none, so a reader that last saw `n` missed lines exactly when `starts_at > n + 1`."""

    generation: str
    starts_at: int
    lines: tuple[WindowLine, ...]
    talked_over: WindowTalkedOver | None = None
    voice: WindowVoice | None = None
    interrupted: str | None = None


def utf8_length(text: str) -> int | None:
    """The bytes the text takes in UTF-8, or nothing when it does not encode (a lone surrogate) or is empty."""
    try:
        return len(text.encode()) or None
    except UnicodeEncodeError:
        return None


class ChatWindow:
    """The newest 100 lines of at most 256 KiB of text, numbered from 1 within one generation."""

    def __init__(self, generation: str) -> None:
        self._generation = generation
        self._lines: deque[tuple[WindowLine, int]] = deque()
        self._text_bytes = 0
        self._next_seq = 1
        self._talked_over: _TalkedOver | None = None
        self._talked_over_count = 0
        self._hold_count = 0
        self._voice: WindowVoice | None = None

    def append(
        self,
        kind: LineKind,
        name: str,
        text: str,
        *,
        own: bool,
        spoken: bool = False,
        cut: bool = False,
        continues: bool = False,
        traceparent: str | None = None,
    ) -> bool:
        """Add the line and evict the oldest past the bounds; say whether it was added. A line whose name or text is
        empty or does not encode as UTF-8 is refused and takes no `seq`. `spoken` marks a sentence heard aloud, `cut`
        one that was cut before its end, `continues` a spoken one whose speaker's next sentence already waits to play,
        and `traceparent` names the trace the line entered in."""
        size = utf8_length(text)
        if size is None or utf8_length(name) is None:
            return False
        line = WindowLine(self._next_seq, kind, name, text, own, spoken, cut, continues, traceparent)
        self._lines.append((line, size))
        self._next_seq += 1
        self._text_bytes += size
        while len(self._lines) > LINES_PER_WINDOW or self._text_bytes > TEXT_BYTES_PER_WINDOW:
            self._text_bytes -= self._lines.popleft()[1]
        return True

    @property
    def last_seq(self) -> int:
        """The `seq` of the newest line, 0 for a window that never held one."""
        return self._next_seq - 1

    def talk_over(self, speaker: str, name: str, *, opens: bool) -> bool:
        """A person starts speaking: they join the record that stands, or open one when `opens`, which is when a
        sentence of the agent plays or waits or the agent is held; say whether the speaker was added. The record keeps
        its id until its last person's turn has ended."""
        record = self._talked_over
        if record is not None and speaker in record.partials:
            return False
        if record is None:
            if not opens:
                return False
            self._talked_over_count += 1
            record = self._talked_over = _TalkedOver(str(self._talked_over_count))
        record.partials[speaker] = WindowPartial(name, "")
        return True

    @property
    def has_record(self) -> bool:
        """Whether a record of people talking over the agent stands."""
        return self._talked_over is not None

    def new_hold_id(self) -> str:
        """The id of a hold that arises: the window's own, which never repeats within its generation."""
        self._hold_count += 1
        return str(self._hold_count)

    def hear_partial(self, speaker: str, text: str) -> bool:
        """The words the speaker has said so far; say whether the record changed. Text that does not encode as UTF-8
        is not taken."""
        record = self._talked_over
        if record is None or speaker not in record.partials:
            return False
        if text and utf8_length(text) is None:
            return False
        partial = record.partials[speaker]
        if partial.text == text:
            return False
        record.partials[speaker] = WindowPartial(partial.name, text)
        return True

    def turn_ended(self, speaker: str) -> bool:
        """The speaker's turn ended, or they can no longer be heard: their partial goes, and the record with its last
        one; say whether the record changed."""
        record = self._talked_over
        if record is None or record.partials.pop(speaker, None) is None:
            return False
        if not record.partials:
            self._talked_over = None
        return True

    def voice_failed(self) -> bool:
        """The agent's voice failed; say whether it turned unavailable just now."""
        turned = self._voice is not WindowVoice.UNAVAILABLE
        self._voice = WindowVoice.UNAVAILABLE
        return turned

    @property
    def voice_unavailable(self) -> bool:
        return self._voice is WindowVoice.UNAVAILABLE

    def voice_worked(self) -> bool:
        """The agent's voice made a sound; say whether that turned an unavailable voice available again."""
        turned = self._voice is WindowVoice.UNAVAILABLE
        if turned:
            self._voice = WindowVoice.AVAILABLE
        return turned

    def read(self, *, interrupted: str | None, playing: bool) -> WindowRead:
        """The window, saying the agent is held under `interrupted` and whether a sentence of it `playing`, which its
        speaking knows."""
        lines = tuple(line for line, _ in self._lines)
        record = self._talked_over
        talked_over = None if record is None else WindowTalkedOver(record.id, playing, tuple(record.partials.values()))
        return WindowRead(
            self._generation,
            lines[0].seq if lines else self._next_seq,
            lines,
            talked_over,
            self._voice,
            interrupted,
        )


@dataclass
class _TalkedOver:
    """The record that stands: its people in the order they started speaking, keyed by the app's speaker."""

    id: str
    partials: dict[str, WindowPartial] = field(default_factory=dict)
