from typing import TYPE_CHECKING

from hopin.skat.frames import BROWSER_FRAMES, EmptyTableFrame
from hopin.skat.vocabulary import SkatSignal
from tests.support.rendered_frames import mapped_signal_names

if TYPE_CHECKING:
    from pathlib import Path


def test_an_empty_table_sends_its_absent_fields_as_explicit_null() -> None:
    assert EmptyTableFrame(4, None, None, None, None).to_json() == {
        "type": SkatSignal.SKAT_TABLE.value,
        "revision": 4,
        "attachment": None,
        "table": None,
        "src": None,
        "opener_id": None,
    }


def test_the_signal_map_has_exactly_the_signals_the_frames_carry(tmp_path: Path) -> None:
    assert mapped_signal_names(tmp_path, "frontend/src/lib/features/skat/wire.ts", "SKAT_SIGNAL") == {
        frame.TYPE.name for frame in BROWSER_FRAMES
    }
