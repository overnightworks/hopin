class OpenStreamError(Exception):
    pass


class RoomNotFoundError(OpenStreamError):
    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room not found: {room_id}")
        self.room_id = room_id


class RoomFullError(OpenStreamError):
    def __init__(self, room_id: str) -> None:
        super().__init__(f"Room is full: {room_id}")
        self.room_id = room_id
