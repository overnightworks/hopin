"""JPEGs for the tests of a look: a header that reads as a picture of the size asked for, padded to the byte size asked
for. No decoder reads them; the server reads the start-of-image marker and the frame header only."""

import base64

from hopin.participation.vocabulary import ParticipationSignal

START_OF_IMAGE = b"\xff\xd8"
END_OF_IMAGE = b"\xff\xd9"
START_OF_SCAN = bytes.fromhex("ffda000c03010002110311003f00")
APPLICATION_SEGMENT = bytes.fromhex("ffe000104a46494600010100000100010000")
COMPONENTS = bytes.fromhex("03012200021101031101")
FRAME_LENGTH = 17


def jpeg_of(width=640, height=480, size=3000, frame_marker=b"\xff\xc0"):
    """A JPEG of `size` bytes whose frame header says it is `width` by `height` pixels."""
    frame = frame_marker + FRAME_LENGTH.to_bytes(2) + b"\x08" + height.to_bytes(2) + width.to_bytes(2) + COMPONENTS
    head = START_OF_IMAGE + APPLICATION_SEGMENT + frame + START_OF_SCAN
    return head + bytes(size - len(head) - len(END_OF_IMAGE)) + END_OF_IMAGE


def parts_of(picture, characters):
    """The base64 text of the picture cut into parts of at most `characters` characters."""
    encoded = base64.b64encode(picture).decode("ascii")
    return [encoded[start : start + characters] for start in range(0, len(encoded), characters)]


def part(request_id, text, index=0, count=1):
    """The browser's frame that carries one part of the picture it answers a look with."""
    return {
        "type": ParticipationSignal.AGENT_LOOK_PART.value,
        "request_id": request_id,
        "index": index,
        "count": count,
        "data": text,
    }
