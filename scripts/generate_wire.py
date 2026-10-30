"""Write frontend/src/lib/wire.ts, the browser's typed copy of the core wire vocabulary.

The Python enums and constants own the vocabulary; this generator renders them as
TypeScript string enums and typed constants. `--check` fails when the committed file
differs from the rendering, so CI catches drift.
"""

import sys
from pathlib import Path
from typing import TYPE_CHECKING

REPOSITORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY / "src"))

from hopin.config.constants import (  # noqa: E402
    AUTH_START_PATH,
    ICE_SERVERS_PATH,
    REPLAY_LOG_LIMIT,
    SESSION_ENDED_CLOSE_CODE,
    WEBSOCKET_PATH,
    ErrorReason,
    RoomClosedReason,
    SignalType,
)
from hopin.signaling.replay import DESCRIPTION_TYPES, SUPERSEDED_BY_A_DESCRIPTION  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Sequence
    from enum import StrEnum

OUTPUT_PATH = REPOSITORY / "frontend" / "src" / "lib" / "wire.ts"
CHECK_FLAG = "--check"
HEADER = "// Generated from the Python wire owners by scripts/generate_wire.py. Do not edit.\n"
INDENT = "    "
PRINT_WIDTH = 120


def _enum(exported_name: str, vocabulary: type[StrEnum]) -> str:
    members = "".join(f'{INDENT}{name} = "{member.value}",\n' for name, member in vocabulary.__members__.items())
    return f"export enum {exported_name} {{\n{members}}}\n"


def _signal_types(exported_name: str, members: frozenset[SignalType]) -> str:
    names = [f"SIGNAL_TYPE.{member.name}" for member in sorted(members)]
    declaration = f"export const {exported_name}: readonly SIGNAL_TYPE[] = ["
    one_line = f"{declaration}{', '.join(names)}];\n"
    if len(one_line) <= PRINT_WIDTH:
        return one_line
    wrapped = "".join(f"{INDENT}{name},\n" for name in names)
    return f"{declaration}\n{wrapped}];\n"


def render() -> str:
    sections = [
        _enum("SIGNAL_TYPE", SignalType),
        _enum("ERROR_REASON", ErrorReason),
        _enum("ROOM_CLOSED_REASON", RoomClosedReason),
        f"export const SESSION_ENDED_CLOSE_CODE = {SESSION_ENDED_CLOSE_CODE};\n"
        f'export const WEBSOCKET_PATH = "{WEBSOCKET_PATH}";\n'
        f'export const ICE_SERVERS_PATH = "{ICE_SERVERS_PATH}";\n'
        f'export const AUTH_START_PATH = "{AUTH_START_PATH}";\n'
        f"export const REPLAY_LOG_LIMIT = {REPLAY_LOG_LIMIT};\n"
        + _signal_types("DESCRIPTION_TYPES", DESCRIPTION_TYPES)
        + _signal_types("SUPERSEDED_BY_A_DESCRIPTION", SUPERSEDED_BY_A_DESCRIPTION),
    ]
    return HEADER + "\n" + "\n".join(sections)


def main(arguments: Sequence[str], output: Path = OUTPUT_PATH) -> int:
    rendered = render()
    if CHECK_FLAG in arguments:
        if not output.is_file() or output.read_text() != rendered:
            raise SystemExit(f"generate_wire: {output} is out of date; run scripts/generate_wire.py without --check")
        return 0
    output.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
