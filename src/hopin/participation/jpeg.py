"""What a look reads of a JPEG: its size from the start-of-frame segment, which precedes every scan. Pure."""

from typing import Final

_START: Final = b"\xff\xd8\xff"
_MARKER_PREFIX: Final = 0xFF
_NOT_A_FRAME: Final = frozenset({0xC4, 0xC8, 0xCC})
_FIRST_FRAME: Final = 0xC0
_LAST_FRAME: Final = 0xCF
_START_OF_SCAN: Final = 0xDA
_END_OF_IMAGE: Final = 0xD9
_NO_LENGTH: Final = frozenset({0x01, *range(0xD0, 0xD9)})
_LENGTH_BYTES: Final = 2
_SIZE_OFFSET: Final = 3
_FRAME_HEADER_BYTES: Final = 7


def size_of(picture: bytes) -> tuple[int, int] | None:
    """The width and height of a JPEG, or nothing when `picture` does not start as one or reaches its first scan with
    no readable frame header."""
    if not picture.startswith(_START):
        return None
    position = 2
    while position + 1 < len(picture):
        if picture[position] != _MARKER_PREFIX:
            return None
        marker = picture[position + 1]
        if marker == _MARKER_PREFIX:
            position += 1
            continue
        position += 2
        if marker in _NO_LENGTH:
            continue
        if marker in (_START_OF_SCAN, _END_OF_IMAGE) or position + _LENGTH_BYTES > len(picture):
            return None
        length = int.from_bytes(picture[position : position + _LENGTH_BYTES])
        if _FIRST_FRAME <= marker <= _LAST_FRAME and marker not in _NOT_A_FRAME:
            return _frame_size(picture[position : position + length])
        position += length
    return None


def _frame_size(segment: bytes) -> tuple[int, int] | None:
    if len(segment) < _FRAME_HEADER_BYTES:
        return None
    height = int.from_bytes(segment[_SIZE_OFFSET : _SIZE_OFFSET + 2])
    width = int.from_bytes(segment[_SIZE_OFFSET + 2 : _SIZE_OFFSET + 4])
    return width, height
