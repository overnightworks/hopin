"""A browser's socket to a `hopin serve` process: its frames in and out, as the page sends them."""

import contextlib
import json
from typing import TYPE_CHECKING

from websockets.sync.client import ClientConnection, connect

from hopin.config.constants import WEBSOCKET_PATH, SignalType
from tests.stores.support.pod_pair import LOCALHOST
from tests.support.signed_in import handshake_headers

if TYPE_CHECKING:
    from tests.stores.support.pod_pair import Pod
    from tests.support.signed_in import SeededMember

FRAME_WAIT_SECONDS = 5
QUIET_SECONDS = 0.3


class SeatSocket:
    """One socket of a member to a pod; entering it opens the seat, leaving it closes the socket."""

    def __init__(self, pod: Pod, member: SeededMember) -> None:
        self._connection: ClientConnection = connect(
            f"ws://{LOCALHOST}:{pod.port}{WEBSOCKET_PATH}", additional_headers=handshake_headers(member)
        )
        self.frames: list[dict] = []

    def send(self, frame: dict) -> None:
        self._connection.send(json.dumps(frame))

    def join(self, room_id: str | None = None) -> dict:
        self.send({"type": SignalType.JOIN.value, "room_id": room_id})
        return self.next_frame()

    def next_frame(self, timeout: float = FRAME_WAIT_SECONDS) -> dict:
        frame = json.loads(self._connection.recv(timeout=timeout))
        self.frames.append(frame)
        return frame

    def frame_of_type(self, signal_type: str, timeout: float = FRAME_WAIT_SECONDS) -> dict:
        """The next frame of the type; the frames before it are kept in `frames`."""
        try:
            while (frame := self.next_frame(timeout))["type"] != signal_type:
                continue
        except TimeoutError:
            seen = [frame["type"] for frame in self.frames]
            raise AssertionError(f"no {signal_type} frame in {timeout} s; the frames were {seen}") from None
        return frame

    def stays_quiet(self) -> bool:
        """Whether the server sends nothing in a moment: a seat that was opened and nothing refused it."""
        try:
            self.next_frame(QUIET_SECONDS)
        except TimeoutError:
            return True
        return False

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._connection.close()
