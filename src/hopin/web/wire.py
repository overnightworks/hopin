import json
from typing import TYPE_CHECKING

from hopin.config.constants import (
    REPLAY_LOG_LIMIT,
    SESSION_ENDED_CLOSE_CODE,
    STATIC_PATH,
    WEBSOCKET_PATH,
    ErrorReason,
    GamePhase,
    GameRefusal,
    RoomClosedReason,
    SignalType,
)
from hopin.signaling.replay import DESCRIPTION_TYPES, SUPERSEDED_BY_A_DESCRIPTION

if TYPE_CHECKING:
    from enum import StrEnum

WIRE_MODULE_PATH = f"{STATIC_PATH}/js/wire.js"
WIRE_MODULE_MEDIA_TYPE = "text/javascript"


def _members(vocabulary: type[StrEnum]) -> dict[str, str]:
    return {name: member.value for name, member in vocabulary.__members__.items()}


def wire_vocabulary() -> dict[str, object]:
    """The protocol tokens the browser shares with the server, by their exported JS name."""
    return {
        "SIGNAL_TYPE": _members(SignalType),
        "ERROR_REASON": _members(ErrorReason),
        "ROOM_CLOSED_REASON": _members(RoomClosedReason),
        "GAME_PHASE": _members(GamePhase),
        "GAME_REFUSAL": _members(GameRefusal),
        "SESSION_ENDED_CLOSE_CODE": SESSION_ENDED_CLOSE_CODE,
        "WEBSOCKET_PATH": WEBSOCKET_PATH,
        "REPLAY_LOG_LIMIT": REPLAY_LOG_LIMIT,
        "DESCRIPTION_TYPES": sorted(DESCRIPTION_TYPES),
        "SUPERSEDED_BY_A_DESCRIPTION": sorted(SUPERSEDED_BY_A_DESCRIPTION),
    }


def render_wire_module() -> str:
    return "".join(f"export const {name} = {json.dumps(value)};\n" for name, value in wire_vocabulary().items())
