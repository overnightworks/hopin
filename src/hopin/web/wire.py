import json
from typing import TYPE_CHECKING

from hopin.config.constants import (
    AUTH_START_PATH,
    REPLAY_LOG_LIMIT,
    RETURN_TO_QUERY_PARAMETER,
    SESSION_ENDED_CLOSE_CODE,
    STATIC_PATH,
    WEBSOCKET_PATH,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from hopin.signaling.features import FeatureClashError
from hopin.signaling.replay import DESCRIPTION_TYPES, SUPERSEDED_BY_A_DESCRIPTION

if TYPE_CHECKING:
    from enum import StrEnum

    from hopin.signaling.features import CallFeature

WIRE_MODULE_PATH = f"{STATIC_PATH}/js/wire.js"
WIRE_MODULE_MEDIA_TYPE = "text/javascript"


def _members(vocabulary: type[StrEnum]) -> dict[str, str]:
    return {name: member.value for name, member in vocabulary.__members__.items()}


def wire_vocabulary(feature: CallFeature | None) -> dict[str, object]:
    """The protocol tokens the browser shares with the server, by their exported JS name, the feature's included."""
    core = {**_core_vocabulary(), "FEATURE_MODULES": [] if feature is None else [feature.browser_module]}
    if feature is None:
        return core
    clashes = sorted(name for name in feature.wire_exports if name in core)
    if clashes:
        raise FeatureClashError(clashes)
    # The handler has refused a feature signal type that names a core one, so none is replaced here.
    return {**core, "SIGNAL_TYPE": {**_members(SignalType), **feature.signal_types}, **feature.wire_exports}


def _core_vocabulary() -> dict[str, object]:
    return {
        "SIGNAL_TYPE": _members(SignalType),
        "ERROR_REASON": _members(ErrorReason),
        "ROOM_CLOSED_REASON": _members(RoomClosedReason),
        "SESSION_ENDED_CLOSE_CODE": SESSION_ENDED_CLOSE_CODE,
        "WEBSOCKET_PATH": WEBSOCKET_PATH,
        "AUTH_START_PATH": AUTH_START_PATH,
        "RETURN_TO_QUERY_PARAMETER": RETURN_TO_QUERY_PARAMETER,
        "REPLAY_LOG_LIMIT": REPLAY_LOG_LIMIT,
        "DESCRIPTION_TYPES": sorted(DESCRIPTION_TYPES),
        "SUPERSEDED_BY_A_DESCRIPTION": sorted(SUPERSEDED_BY_A_DESCRIPTION),
    }


def render_wire_module(feature: CallFeature | None) -> str:
    return "".join(f"export const {name} = {json.dumps(value)};\n" for name, value in wire_vocabulary(feature).items())
