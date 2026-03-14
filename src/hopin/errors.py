class HopInError(Exception):
    pass


class RoomNotFoundError(HopInError):
    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room not found: {room_id}")
        self.room_id = room_id


class RoomFullError(HopInError):
    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room is full: {room_id}")
        self.room_id = room_id


class RoomLockedError(HopInError):
    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room is locked: {room_id}")
        self.room_id = room_id


class WrongPasswordError(HopInError):
    def __init__(self, room_id: str) -> None:
        super().__init__(f"Wrong password for room: {room_id}")
        self.room_id = room_id


class NotHostError(HopInError):
    def __init__(self, room_id: str) -> None:
        super().__init__(f"Only the host can perform this action in room: {room_id}")
        self.room_id = room_id
