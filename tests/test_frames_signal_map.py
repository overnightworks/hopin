import re
from typing import TYPE_CHECKING

import pytest

from hopin.participation.frames import BROWSER_FRAMES as PARTICIPATION_FRAMES
from hopin.skat.frames import BROWSER_FRAMES as SKAT_FRAMES
from scripts import generate_wire

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("output", "signal_constant", "frames"),
    [
        ("frontend/src/lib/features/skat/wire.ts", "SKAT_SIGNAL", SKAT_FRAMES),
        ("frontend/src/lib/features/participation/wire.ts", "PARTICIPATION_SIGNAL", PARTICIPATION_FRAMES),
    ],
    ids=["skat", "participation"],
)
def test_the_signal_map_has_exactly_the_signals_the_frames_carry(
    tmp_path: Path, output: str, signal_constant: str, frames: tuple[type, ...]
) -> None:
    written = generate_wire.outputs(tmp_path)[tmp_path / output]

    mapped = set(re.findall(rf"^    \[{signal_constant}\.(\w+)\]:", written, flags=re.MULTILINE))
    assert mapped == {frame.TYPE.name for frame in frames}
