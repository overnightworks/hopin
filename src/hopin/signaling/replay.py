from collections import deque
from typing import cast

from hopin.config.constants import REPLAY_LOG_LIMIT, SignalType
from hopin.signaling.frames import SENDER_KEY, SEQUENCE_KEY, TYPE_KEY, JsonObject


def sequence_of(frame: JsonObject) -> int:
    return cast("int", frame[SEQUENCE_KEY])


DESCRIPTION_TYPES = frozenset({SignalType.OFFER, SignalType.ANSWER})
SUPERSEDED_BY_A_DESCRIPTION = DESCRIPTION_TYPES | {SignalType.ICE_CANDIDATE}


class ReplayLog:
    """Numbers the frames sent to one seat and keeps the newest ones for a resume."""

    def __init__(self, limit: int = REPLAY_LOG_LIMIT) -> None:
        self._limit = limit
        self._frames: deque[JsonObject] = deque()
        self._last_seq = 0
        self._floor = 0

    def __len__(self) -> int:
        return len(self._frames)

    def append(self, frame: JsonObject) -> JsonObject:
        if frame[TYPE_KEY] in DESCRIPTION_TYPES:
            self._drop_superseded_by(frame)
        self._last_seq += 1
        sequenced = {**frame, SEQUENCE_KEY: self._last_seq}
        self._frames.append(sequenced)
        while len(self._frames) > self._limit:
            self._floor = sequence_of(self._frames.popleft())
        return sequenced

    def reaches_back_to(self, last_seq: int) -> bool:
        return last_seq >= self._floor

    def since(self, last_seq: int) -> list[JsonObject]:
        return [frame for frame in self._frames if sequence_of(frame) > last_seq]

    def _drop_superseded_by(self, description: JsonObject) -> None:
        sender = description.get(SENDER_KEY)
        self._frames = deque(
            frame
            for frame in self._frames
            if not (frame[TYPE_KEY] in SUPERSEDED_BY_A_DESCRIPTION and frame.get(SENDER_KEY) == sender)
        )
