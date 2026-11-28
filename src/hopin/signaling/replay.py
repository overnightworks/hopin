from dataclasses import dataclass

from hopin.config.constants import SignalType
from hopin.signaling.frames import SENDER_KEY, TYPE_KEY, JsonObject

# The one key besides its type and its sender that a frame is held by: the agent whose frame it is.
HOLDING_KEY = "agent_id"


@dataclass(frozen=True)
class SequencedFrame:
    seq: int
    frame: JsonObject


@dataclass(frozen=True)
class Supersedes:
    """The earlier frames in a seat's log that a frame makes moot, so they are dropped as it is logged: those of
    `types`, every frame when `types` is None, only `sender`'s when it names one, and only those of the agent the
    `holding` value names, when it names one. A frame whose write has begun is written all the same."""

    types: frozenset[str] | None
    sender: str | None = None
    holding: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if self.holding is not None and self.holding[0] != HOLDING_KEY:
            message = f"A frame is held by {HOLDING_KEY} alone, not by {self.holding[0]}"
            raise ValueError(message)


DESCRIPTION_TYPES = frozenset({SignalType.OFFER, SignalType.ANSWER})
SUPERSEDED_BY_A_DESCRIPTION = DESCRIPTION_TYPES | {SignalType.ICE_CANDIDATE}


def superseded_by(frame: JsonObject, supersedes: Supersedes | None) -> Supersedes | None:
    """What logging the frame drops from the seat's log: a description supersedes its sender's earlier descriptions and
    candidates, whatever else it names."""
    if frame[TYPE_KEY] in DESCRIPTION_TYPES:
        return Supersedes(SUPERSEDED_BY_A_DESCRIPTION, sender=str(frame[SENDER_KEY]))
    return supersedes
