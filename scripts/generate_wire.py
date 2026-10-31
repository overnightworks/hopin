"""Write the browser's typed copies of the wire vocabulary: the core's, and the game's beside the game.

The Python enums and constants own the vocabulary; this generator renders them as
TypeScript string enums and typed constants. `--check` writes nothing and fails when a
committed file differs from the rendering or is missing, so CI catches drift.
"""

import dataclasses
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, get_args

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
from hopin.signaling.frames import RoomClosedFrame  # noqa: E402
from hopin.signaling.replay import DESCRIPTION_TYPES, SUPERSEDED_BY_A_DESCRIPTION  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from enum import StrEnum

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


ROOM_CLOSED_FIELD_TYPES = {RoomClosedReason: "ROOM_CLOSED_REASON", int: "number"}


def _room_closed_frame() -> str:
    """Render RoomClosedFrame's fields; a field type missing from ROOM_CLOSED_FIELD_TYPES fails the generation."""
    lines = [f"{INDENT}type: SIGNAL_TYPE.{RoomClosedFrame.TYPE.name};\n"]
    for field in dataclasses.fields(RoomClosedFrame):
        present_types = [part for part in get_args(field.type) if part is not types.NoneType] or [field.type]
        optional = "?" if field.default is not dataclasses.MISSING else ""
        lines.append(f"{INDENT}{field.name}{optional}: {ROOM_CLOSED_FIELD_TYPES[present_types[0]]};\n")
    return "export type RoomClosedFrame = {\n" + "".join(lines) + "};\n"


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
    return _generated_file(f"{enums}\n{_room_closed_frame()}\n{constants}")


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
