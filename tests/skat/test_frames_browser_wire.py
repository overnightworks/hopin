import pytest

from hopin.skat.frames import BROWSER_FRAMES, AttachedTableFrame, EmptyTableFrame, RefusedFrame, SkatFrame
from hopin.skat.vocabulary import BROWSER_ENUMS, SkatRefusal, SkatSignal
from tests.support.rendered_frames import rendered_property_names

FULLY_SET_FRAMES = [
    AttachedTableFrame(3, 1, "t1", "https://skat.example/t/t1?mode=call", "p1"),
    EmptyTableFrame(4, None, None, None, None),
    RefusedFrame(SkatRefusal.BUSY),
]


def test_every_browser_frame_has_a_fully_set_instance_here() -> None:
    assert {type(frame) for frame in FULLY_SET_FRAMES} == set(BROWSER_FRAMES)


@pytest.mark.parametrize("frame", FULLY_SET_FRAMES, ids=lambda frame: type(frame).__name__)
def test_a_frames_rendered_properties_are_the_keys_it_sends_in_order(frame: SkatFrame) -> None:
    assert rendered_property_names(frame, BROWSER_ENUMS) == list(frame.to_json())


def test_an_empty_table_sends_its_absent_fields_as_explicit_null() -> None:
    assert EmptyTableFrame(4, None, None, None, None).to_json() == {
        "type": SkatSignal.SKAT_TABLE.value,
        "revision": 4,
        "attachment": None,
        "table": None,
        "src": None,
        "opener_id": None,
    }
