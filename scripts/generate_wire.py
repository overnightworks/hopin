"""Write the browser's typed copies of the wire vocabulary: the core's, and the game's beside the game.

The Python enums and constants own the vocabulary; this generator renders them as
TypeScript string enums and typed constants. `--check` writes nothing and fails when a
committed file differs from the rendering or is missing, so CI catches drift.
"""

import dataclasses
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, Union, get_args, get_origin

REPOSITORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY / "src"))

from hopin.config.constants import (  # noqa: E402
    ACCOUNT_CONSOLE_PATH,
    ACCOUNT_PATH,
    AUTH_START_PATH,
    CALL_FEATURES_PATH,
    ICE_SERVERS_PATH,
    LOGOUT_PATH,
    REPLAY_LOG_LIMIT,
    RETURN_TO_QUERY_PARAMETER,
    SESSION_ENDED_CLOSE_CODE,
    WEBSOCKET_PATH,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from hopin.game.vocabulary import BROWSER_ENUMS  # noqa: E402  # game
from hopin.signaling.frames import (  # noqa: E402
    SENDER_KEY,
    AnswerRelayFrame,
    ChatBroadcastFrame,
    ErrorFrame,
    ExistingParticipantsFrame,
    IceCandidateRelayFrame,
    JsonValue,
    MediaStateBroadcastFrame,
    OfferRelayFrame,
    ParticipantJoinedFrame,
    ParticipantLeftFrame,
    PeerId,
    RoomClosedFrame,
    RoomCreatedFrame,
    RoomLockedFrame,
    RoomUnlockedFrame,
)
from hopin.signaling.replay import DESCRIPTION_TYPES, SUPERSEDED_BY_A_DESCRIPTION  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from enum import StrEnum

    from _typeshed import DataclassInstance

    class ServerFrame(DataclassInstance, Protocol):
        TYPE: ClassVar[SignalType]


CORE_OUTPUT = Path("frontend/src/lib/wire.ts")
CHECK_FLAG = "--check"
HEADER = "// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.\n"
INDENT = "    "
PRINT_WIDTH = 120


def _enum(exported_name: str, vocabulary: type[StrEnum]) -> str:
    members = "".join(f'{INDENT}{name} = "{member.value}",\n' for name, member in vocabulary.__members__.items())
    return f"export enum {exported_name} {{\n{members}}}\n"


def _enums(vocabularies: Mapping[str, type[StrEnum]]) -> str:
    return "\n".join(_enum(exported_name, vocabulary) for exported_name, vocabulary in vocabularies.items())


def _signal_types(exported_name: str, members: frozenset[SignalType]) -> str:
    names = [f"SIGNAL_TYPE.{member.name}" for member in sorted(members)]
    declaration = f"export const {exported_name}: readonly SIGNAL_TYPE[] = ["
    one_line = f"{declaration}{', '.join(names)}];"
    if len(one_line) <= PRINT_WIDTH:
        return f"{one_line}\n"
    wrapped = "".join(f"{INDENT}{name},\n" for name in names)
    return f"{declaration}\n{wrapped}];\n"


def _generated_file(body: str) -> str:
    return f"{HEADER}\n{body}"


FRAME_FIELD_TYPES: dict[object, str] = {
    str: "string",
    PeerId: "string",
    bool: "boolean",
    int: "number",
    list[str]: "string[]",
    dict[str, str]: "Record<string, string>",
}
ROOM_CLOSED_FIELD_TYPES: dict[object, str] = {RoomClosedReason: "ROOM_CLOSED_REASON"}
ERROR_FIELD_TYPES: dict[object, str] = {ErrorReason: "ERROR_REASON"}
RELAY_FIELD_TYPES: dict[object, str] = {JsonValue: "unknown"}
WIRE_FIELD_NAMES = {"from_": SENDER_KEY}


def render_frame_type(
    frame_cls: type[ServerFrame],
    extra_field_types: Mapping[object, str] | None = None,
) -> str:
    """Render a server frame from its dataclass fields; an unmapped field type fails the generation."""
    field_types = {**FRAME_FIELD_TYPES, **(extra_field_types or {})}
    lines = [f"{INDENT}type: SIGNAL_TYPE.{frame_cls.TYPE.name};\n"]
    for field in dataclasses.fields(frame_cls):
        annotation = field.type
        if get_origin(annotation) in (types.UnionType, Union):
            present_types = [part for part in get_args(annotation) if part is not types.NoneType]
            if len(present_types) != 1:
                message = f"{frame_cls.__name__}.{field.name} must be one type, optionally None, not {annotation}"
                raise TypeError(message)
            annotation = present_types[0]
        if annotation not in field_types:
            message = f"{frame_cls.__name__}.{field.name} has no TypeScript type for {annotation}"
            raise TypeError(message)
        optional = "?" if field.default is not dataclasses.MISSING else ""
        lines.append(f"{INDENT}{WIRE_FIELD_NAMES.get(field.name, field.name)}{optional}: {field_types[annotation]};\n")
    return f"export type {frame_cls.__name__} = {{\n" + "".join(lines) + "};\n"


def _server_frames() -> str:
    return "\n".join(
        (
            render_frame_type(RoomClosedFrame, ROOM_CLOSED_FIELD_TYPES),
            render_frame_type(ErrorFrame, ERROR_FIELD_TYPES),
            render_frame_type(RoomCreatedFrame),
            render_frame_type(ExistingParticipantsFrame),
            render_frame_type(ParticipantJoinedFrame),
            render_frame_type(ParticipantLeftFrame),
            render_frame_type(RoomLockedFrame),
            render_frame_type(RoomUnlockedFrame),
            render_frame_type(ChatBroadcastFrame),
            render_frame_type(MediaStateBroadcastFrame),
            render_frame_type(OfferRelayFrame, RELAY_FIELD_TYPES),
            render_frame_type(AnswerRelayFrame, RELAY_FIELD_TYPES),
            render_frame_type(IceCandidateRelayFrame, RELAY_FIELD_TYPES),
        )
    )


def render_core() -> str:
    enums = _enums({"SIGNAL_TYPE": SignalType, "ERROR_REASON": ErrorReason, "ROOM_CLOSED_REASON": RoomClosedReason})
    constants = (
        f"export const SESSION_ENDED_CLOSE_CODE = {SESSION_ENDED_CLOSE_CODE};\n"
        f'export const WEBSOCKET_PATH = "{WEBSOCKET_PATH}";\n'
        f'export const ICE_SERVERS_PATH = "{ICE_SERVERS_PATH}";\n'
        f'export const CALL_FEATURES_PATH = "{CALL_FEATURES_PATH}";\n'
        f'export const AUTH_START_PATH = "{AUTH_START_PATH}";\n'
        f'export const LOGOUT_PATH = "{LOGOUT_PATH}";\n'
        f'export const ACCOUNT_PATH = "{ACCOUNT_PATH}";\n'
        f'export const ACCOUNT_CONSOLE_PATH = "{ACCOUNT_CONSOLE_PATH}";\n'
        f'export const RETURN_TO_QUERY_PARAMETER = "{RETURN_TO_QUERY_PARAMETER}";\n'
        f"export const REPLAY_LOG_LIMIT = {REPLAY_LOG_LIMIT};\n"
        + _signal_types("DESCRIPTION_TYPES", DESCRIPTION_TYPES)
        + _signal_types("SUPERSEDED_BY_A_DESCRIPTION", SUPERSEDED_BY_A_DESCRIPTION)
    )
    return _generated_file(f"{enums}\n{_server_frames()}\n{constants}")


def outputs(root: Path) -> dict[Path, str]:
    """Every generated file under the root, by path."""
    return {
        root / CORE_OUTPUT: render_core(),
        root / "frontend/src/lib/features/game/wire.ts": _generated_file(_enums(BROWSER_ENUMS)),  # game
    }


def main(arguments: Sequence[str], root: Path = REPOSITORY) -> int:
    rendered = outputs(root)
    if CHECK_FLAG in arguments:
        stale = [path for path, content in rendered.items() if not path.is_file() or path.read_text() != content]
        if stale:
            names = ", ".join(str(path) for path in stale)
            raise SystemExit(f"generate_wire: {names} out of date; run scripts/generate_wire.py without --check")
        return 0
    for path, content in rendered.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
