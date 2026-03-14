import secrets
import string

from openstream.config.constants import ROOM_ID_LENGTH
from openstream.errors import RoomFullError, RoomNotFoundError
from openstream.rooms.models import Room, Viewer


class RoomManager:
    def __init__(self, max_viewers_per_room: int) -> None:
        self._rooms: dict[str, Room] = {}
        self._max_viewers_per_room = max_viewers_per_room

    def _generate_room_id(self) -> str:
        alphabet = string.ascii_lowercase + string.digits
        while True:
            room_id = "".join(secrets.choice(alphabet) for _ in range(ROOM_ID_LENGTH))
            if room_id not in self._rooms:
                return room_id

    def create_room(self, streamer_id: str) -> Room:
        room_id = self._generate_room_id()
        room = Room(room_id=room_id, streamer_id=streamer_id)
        self._rooms[room_id] = room
        return room

    def get_room(self, room_id: str) -> Room:
        room = self._rooms.get(room_id)
        if room is None:
            raise RoomNotFoundError(room_id)
        return room

    def add_viewer(self, room_id: str, viewer_id: str) -> Room:
        room = self.get_room(room_id)
        if room.viewer_count >= self._max_viewers_per_room:
            raise RoomFullError(room_id)
        room.viewers[viewer_id] = Viewer(viewer_id=viewer_id)
        return room

    def remove_viewer(self, room_id: str, viewer_id: str) -> Room:
        room = self.get_room(room_id)
        room.viewers.pop(viewer_id, None)
        return room

    def remove_room(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)

    def has_room(self, room_id: str) -> bool:
        return room_id in self._rooms

    @property
    def room_count(self) -> int:
        return len(self._rooms)
