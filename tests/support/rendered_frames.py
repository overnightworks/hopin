import re
from typing import TYPE_CHECKING

from scripts import generate_wire

if TYPE_CHECKING:
    from pathlib import Path


def mapped_signal_names(tmp_path: Path, output: str, signal_constant: str) -> set[str]:
    """The signals the generated signal map of the output file keys."""
    written = generate_wire.outputs(tmp_path)[tmp_path / output]
    return set(re.findall(rf"^    \[{signal_constant}\.(\w+)\]:", written, flags=re.MULTILINE))
