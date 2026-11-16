import re
from typing import TYPE_CHECKING

from scripts import generate_wire

if TYPE_CHECKING:
    from collections.abc import Mapping
    from enum import IntEnum, StrEnum

    from scripts.generate_wire import ServerFrame


def rendered_property_names(frame: ServerFrame, enums: Mapping[str, type[StrEnum | IntEnum]]) -> list[str]:
    """The properties the generator writes for the frame's type, in order."""
    rendered = generate_wire.render_frame_type(type(frame), None, enums)
    return re.findall(r"^    (\w+)\??:", rendered, flags=re.MULTILINE)
