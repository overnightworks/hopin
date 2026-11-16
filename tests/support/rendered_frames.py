import re
from typing import TYPE_CHECKING

from scripts import generate_wire

if TYPE_CHECKING:
    from collections.abc import Mapping
    from enum import IntEnum, StrEnum
    from pathlib import Path

    from scripts.generate_wire import ServerFrame


def rendered_property_names(frame: ServerFrame, enums: Mapping[str, type[StrEnum | IntEnum]]) -> list[str]:
    """The properties the generator writes for the frame's type, in order."""
    rendered = generate_wire.render_frame_type(type(frame), None, enums)
    return re.findall(r"^    (\w+)\??:", rendered, flags=re.MULTILINE)


def mapped_signal_names(tmp_path: Path, output: str, signal_constant: str) -> set[str]:
    """The signals the generated signal map of the output file keys."""
    written = generate_wire.outputs(tmp_path)[tmp_path / output]
    return set(re.findall(rf"^    \[{signal_constant}\.(\w+)\]:", written, flags=re.MULTILINE))
