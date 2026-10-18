from typing import ClassVar

from hopin.config.constants import ErrorReason


class HopInError(Exception):
    reason: ClassVar[ErrorReason]


class RoomNotFoundError(HopInError):
    reason = ErrorReason.NOT_FOUND

    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room not found: {room_id}")
        self.room_id = room_id


class RoomFullError(HopInError):
    reason = ErrorReason.FULL

    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room is full: {room_id}")
        self.room_id = room_id


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

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
