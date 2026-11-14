import pytest

from hopin.participation.jpeg import size_of
from tests.support.pictures import APPLICATION_SEGMENT, START_OF_IMAGE, START_OF_SCAN, jpeg_of

TABLE = bytes.fromhex("ffc400040000")


class TestTheSizeOfAJpeg:
    @pytest.mark.parametrize("marker", [b"\xff\xc0", b"\xff\xc1", b"\xff\xc2", b"\xff\xcf"], ids=str)
    def test_the_frame_header_of_any_frame_kind_names_the_size(self, marker):
        assert size_of(jpeg_of(1280, 720, frame_marker=marker)) == (1280, 720)

    def test_a_table_before_the_frame_header_is_not_a_frame(self):
        frame = jpeg_of(300, 200)[len(START_OF_IMAGE) + len(APPLICATION_SEGMENT) :]

        assert size_of(START_OF_IMAGE + APPLICATION_SEGMENT + TABLE + frame) == (300, 200)

    def test_fill_bytes_and_markers_without_a_length_before_the_frame_header_are_passed(self):
        frame = jpeg_of(300, 200)[len(START_OF_IMAGE) + len(APPLICATION_SEGMENT) :]

        assert size_of(START_OF_IMAGE + b"\xff\xff\xff\xd0" + APPLICATION_SEGMENT + frame) == (300, 200)

    @pytest.mark.parametrize(
        "picture",
        [
            pytest.param(b"", id="empty"),
            pytest.param(b"GIF89a", id="not_a_jpeg"),
            pytest.param(START_OF_IMAGE + b"\xff", id="cut_at_a_marker"),
            pytest.param(START_OF_IMAGE + b"\xff\xe0", id="cut_before_a_length"),
            pytest.param(START_OF_IMAGE + b"\xff\xc0\x00\x05\x08", id="a_frame_header_too_short"),
            pytest.param(START_OF_IMAGE + APPLICATION_SEGMENT + START_OF_SCAN, id="a_scan_before_any_frame"),
            pytest.param(START_OF_IMAGE + APPLICATION_SEGMENT + b"\x00\x01", id="no_marker_where_one_belongs"),
            pytest.param(START_OF_IMAGE + APPLICATION_SEGMENT + b"\xff\xd9", id="the_end_before_any_frame"),
        ],
    )
    def test_a_picture_with_no_readable_frame_header_has_no_size(self, picture):
        assert size_of(picture) is None
