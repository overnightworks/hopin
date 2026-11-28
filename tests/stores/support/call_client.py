"""A browser's socket to a `hopin serve` process: its frames in and out, as the page sends them."""

import contextlib
import json
import socket
from typing import TYPE_CHECKING

from websockets.exceptions import ConnectionClosed
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

    def close_code(self, timeout: float = FRAME_WAIT_SECONDS) -> int:
        """The code the server closed the socket with, the frames before it kept in `frames`."""
        try:
            while True:
                self.next_frame(timeout)
        except ConnectionClosed as closed:
            if closed.rcvd is None:
                raise AssertionError("the socket closed without a close frame") from closed
            return closed.rcvd.code
        except TimeoutError:
            raise AssertionError(f"the socket stayed open for {timeout} s") from None

    def drop(self) -> None:
        """The connection breaks without a goodbye, as a page's network does."""
        with contextlib.suppress(OSError):
            self._connection.socket.shutdown(socket.SHUT_RDWR)
        self.close()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._connection.close()


class Browser:
    """A member's page: its frames numbered as the page numbers them, and its seat, which it resumes over a new socket
    after a drop."""

    def __init__(self, pod: Pod, member: SeededMember) -> None:
        self._pod = pod
        self._member = member
        self.sockets: list[SeatSocket] = []
        self.peer_id = ""
        self.resume_token = ""
        self.room_id = ""
        self._sent_count = 0

    @property
    def socket(self) -> SeatSocket:
        return self.sockets[-1]

    @property
    def received_seq(self) -> int:
        return max((frame["seq"] for each in self.sockets for frame in each.frames if "seq" in frame), default=0)

    def open(self) -> SeatSocket:
        self.sockets.append(SeatSocket(self._pod, self._member))
        return self.socket

    def join(self, room_id: str | None = None) -> dict:
        reply = self.open().join(room_id)
        self.peer_id, self.resume_token = reply["participant_id"], reply["resume_token"]
        self.room_id = reply.get("room_id", room_id)
        return reply

    def say(self, frame: dict) -> None:
        self._sent_count += 1
        self.socket.send({**frame, "seq": self._sent_count})

    def resume_frame(self, **overrides: object) -> dict:
        return {
            "type": SignalType.RESUME.value,
            "participant_id": self.peer_id,
            "token": self.resume_token,
            "last_seq": self.received_seq,
            "sent_floor": 0,
            **overrides,
        }

    def resume(self, **overrides: object) -> SeatSocket:
        """A new socket that claims the seat; the page keeps the token the server answers with."""
        self.open().send(self.resume_frame(**overrides))
        return self.socket

    def resumed(self) -> dict:
        """The server's answer to the resume, whose token the page keeps from now on."""
        frame = self.socket.frame_of_type(SignalType.RESUMED.value)
        self.resume_token = frame["resume_token"]
        return frame
