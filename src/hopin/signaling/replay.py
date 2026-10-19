from collections import deque
from typing import Any

from hopin.config.constants import REPLAY_LOG_LIMIT, SignalType

SEQUENCE_KEY = "seq"
SENDER_KEY = "from"
TYPE_KEY = "type"

_DESCRIPTION_TYPES = frozenset({SignalType.OFFER, SignalType.ANSWER})
_SUPERSEDED_BY_A_DESCRIPTION = _DESCRIPTION_TYPES | {SignalType.ICE_CANDIDATE}


class ReplayLog:
    """Numbers the frames sent to one seat and keeps the newest ones for a resume."""

    def __init__(self, limit: int = REPLAY_LOG_LIMIT) -> None:
        self._limit = limit
        self._frames: deque[dict[str, Any]] = deque()
        self._last_seq = 0
        self._floor = 0

    def append(self, frame: dict[str, Any]) -> dict[str, Any]:
        if frame[TYPE_KEY] in _DESCRIPTION_TYPES:
            self._drop_superseded_by(frame)
        self._last_seq += 1
        sequenced = {**frame, SEQUENCE_KEY: self._last_seq}
        self._frames.append(sequenced)
        while len(self._frames) > self._limit:
            self._floor = self._frames.popleft()[SEQUENCE_KEY]
        return sequenced

    def reaches_back_to(self, last_seq: int) -> bool:
        return last_seq >= self._floor

    def since(self, last_seq: int) -> list[dict[str, Any]]:
        return [frame for frame in self._frames if frame[SEQUENCE_KEY] > last_seq]

    def _drop_superseded_by(self, description: dict[str, Any]) -> None:
        sender = description.get(SENDER_KEY)
        self._frames = deque(
            frame
            for frame in self._frames
            if not (frame[TYPE_KEY] in _SUPERSEDED_BY_A_DESCRIPTION and frame.get(SENDER_KEY) == sender)
        )
