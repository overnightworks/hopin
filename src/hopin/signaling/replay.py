from collections import deque
from dataclasses import dataclass

from hopin.config.constants import REPLAY_LOG_LIMIT, SignalType
from hopin.signaling.frames import SENDER_KEY, SEQUENCE_KEY, TYPE_KEY, JsonObject


@dataclass(frozen=True)
class SequencedFrame:
    seq: int
    frame: JsonObject


@dataclass(frozen=True)
class Supersedes:
    """The earlier frames in a seat's queue that a frame makes moot, so they are dropped as it is queued: those of
    `types`, every frame when `types` is None, only `sender`'s when it names one, and only those whose key holds the
    value `holding` names, when it names one. A frame whose write has begun is written all the same."""

    types: frozenset[str] | None
    sender: str | None = None
    holding: tuple[str, str] | None = None

    def covers(self, frame: JsonObject) -> bool:
        return (
            (self.types is None or frame[TYPE_KEY] in self.types)
            and (self.sender is None or frame.get(SENDER_KEY) == self.sender)
            and (self.holding is None or frame.get(self.holding[0]) == self.holding[1])
        )


DESCRIPTION_TYPES = frozenset({SignalType.OFFER, SignalType.ANSWER})
SUPERSEDED_BY_A_DESCRIPTION = DESCRIPTION_TYPES | {SignalType.ICE_CANDIDATE}


class ReplayLog:
    """Numbers the frames sent to one seat and keeps the newest ones for a resume."""

    def __init__(self, limit: int = REPLAY_LOG_LIMIT) -> None:
        self._limit = limit
        self._frames: deque[SequencedFrame] = deque()
        self._last_seq = 0
        self._floor = 0

    def __len__(self) -> int:
        return len(self._frames)

    def append(self, frame: JsonObject, supersedes: Supersedes | None = None) -> JsonObject:
        """Number the frame and keep it, after dropping the frames it supersedes; a description supersedes its
        sender's earlier descriptions and candidates."""
        if frame[TYPE_KEY] in DESCRIPTION_TYPES:
            supersedes = Supersedes(SUPERSEDED_BY_A_DESCRIPTION, sender=str(frame[SENDER_KEY]))
        if supersedes is not None:
            self._frames = deque(kept for kept in self._frames if not supersedes.covers(kept.frame))
        self._last_seq += 1
        sequenced = SequencedFrame(self._last_seq, {**frame, SEQUENCE_KEY: self._last_seq})
        self._frames.append(sequenced)
        while len(self._frames) > self._limit:
            self._floor = self._frames.popleft().seq
        return sequenced.frame

    def reaches_back_to(self, last_seq: int) -> bool:
        return last_seq >= self._floor

    def since(self, last_seq: int) -> list[SequencedFrame]:
        return [kept for kept in self._frames if kept.seq > last_seq]
