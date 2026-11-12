"""The window of a call's chat one joined agent reads: the lines said in its room since it joined, bounded.

Plain state with no clock and no I/O. It lives and dies with the agent's place and keeps no history beyond its
bounds.
"""

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

LINES_PER_WINDOW: Final = 100
TEXT_BYTES_PER_WINDOW: Final = 256 * 1024


class LineKind(StrEnum):
    """Who said a line: a person in the call or an agent in it."""

    PERSON = "person"
    AGENT = "agent"


@dataclass(frozen=True)
class WindowLine:
    seq: int
    kind: LineKind
    name: str
    text: str
    own: bool
    spoken: bool = False


@dataclass(frozen=True)
class WindowRead:
    """The lines a window holds, oldest first. `starts_at` is the `seq` of the first, or of the next line when there
    is none, so a reader that last saw `n` missed lines exactly when `starts_at > n + 1`."""

    generation: str
    starts_at: int
    lines: tuple[WindowLine, ...]


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

    def append(self, kind: LineKind, name: str, text: str, *, own: bool, spoken: bool = False) -> bool:
        """Add the line and evict the oldest past the bounds; say whether it was added. A line whose name or text is
        empty or does not encode as UTF-8 is refused and takes no `seq`. `spoken` marks a sentence heard aloud."""
        size = utf8_length(text)
        if size is None or utf8_length(name) is None:
            return False
        self._lines.append((WindowLine(self._next_seq, kind, name, text, own, spoken), size))
        self._next_seq += 1
        self._text_bytes += size
        while len(self._lines) > LINES_PER_WINDOW or self._text_bytes > TEXT_BYTES_PER_WINDOW:
            self._text_bytes -= self._lines.popleft()[1]
        return True

    def read(self) -> WindowRead:
        lines = tuple(line for line, _ in self._lines)
        return WindowRead(self._generation, lines[0].seq if lines else self._next_seq, lines)
