from collections import deque
from dataclasses import dataclass

from hopin.config.constants import REPLAY_LOG_LIMIT, SignalType
from hopin.signaling.frames import SENDER_KEY, SEQUENCE_KEY, TYPE_KEY, JsonObject


@dataclass(frozen=True)
class SequencedFrame:
    seq: int
    frame: JsonObject


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

    def append(self, frame: JsonObject) -> JsonObject:
        if frame[TYPE_KEY] in DESCRIPTION_TYPES:
            self._drop_superseded_by(frame)
        self._last_seq += 1
        sequenced = SequencedFrame(self._last_seq, {**frame, SEQUENCE_KEY: self._last_seq})
        self._frames.append(sequenced)
        while len(self._frames) > self._limit:
            self._floor = self._frames.popleft().seq
        return sequenced.frame

    def reaches_back_to(self, last_seq: int) -> bool:
        return last_seq >= self._floor

    def since(self, last_seq: int) -> list[JsonObject]:
        return [kept.frame for kept in self._frames if kept.seq > last_seq]

    def seq_after(self, last_seq: int) -> int:
        """The seq of the oldest kept frame past `last_seq`; only call when `since` is not empty."""
        return next(kept.seq for kept in self._frames if kept.seq > last_seq)

    def _drop_superseded_by(self, description: JsonObject) -> None:
        sender = description.get(SENDER_KEY)
        self._frames = deque(
            kept
            for kept in self._frames
            if not (kept.frame[TYPE_KEY] in SUPERSEDED_BY_A_DESCRIPTION and kept.frame.get(SENDER_KEY) == sender)
        )
