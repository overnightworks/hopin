from typing import Any, ClassVar

from hopin.config.constants import ErrorReason, ResumeRefusalCause


class HopInError(Exception):
    reason: ClassVar[ErrorReason]

    def frame_fields(self) -> dict[str, Any]:
        return {}


class RoomNotFoundError(HopInError):
    reason = ErrorReason.NOT_FOUND

    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room not found: {room_id}")
        self.room_id = room_id


class RoomFullError(HopInError):
    reason = ErrorReason.FULL

    def __init__(self, room_id: str, capacity: int) -> None:
        super().__init__(f"Room is full: {room_id}")
        self.room_id = room_id
        self.capacity = capacity

    def frame_fields(self) -> dict[str, Any]:
        return {"capacity": self.capacity}


class RoomLockedError(HopInError):
    reason = ErrorReason.LOCKED

    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room is locked: {room_id}")
        self.room_id = room_id


class NotHostError(HopInError):
    reason = ErrorReason.NOT_HOST

    def __init__(self, room_id: str) -> None:
        super().__init__(f"Only the host can perform this action in room: {room_id}")
        self.room_id = room_id


class InvalidMessageError(HopInError):
    reason = ErrorReason.INVALID_MESSAGE


class ResumeRefusedError(HopInError):
    reason = ErrorReason.RESUME_REFUSED

    def __init__(self, cause: ResumeRefusalCause) -> None:
        super().__init__("Resume refused")
        self.cause = cause
