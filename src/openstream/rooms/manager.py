import secrets
import string

from openstream.config.constants import ROOM_ID_LENGTH
from openstream.errors import NotHostError, RoomFullError, RoomLockedError, RoomNotFoundError, WrongPasswordError
from openstream.rooms.models import Participant, Room


class RoomManager:
    def __init__(self, max_participants_per_room: int) -> None:
        self._rooms: dict[str, Room] = {}
        self._max_participants_per_room = max_participants_per_room

    def _generate_room_id(self) -> str:
        alphabet = string.ascii_lowercase + string.digits
        while True:
            room_id = "".join(secrets.choice(alphabet) for _ in range(ROOM_ID_LENGTH))
            if room_id not in self._rooms:
                return room_id

    def create_room(self, host_id: str, password: str | None = None) -> Room:
        room_id = self._generate_room_id()
        room = Room(room_id=room_id, host_id=host_id, password=password)
        room.participants[host_id] = Participant(participant_id=host_id)
        self._rooms[room_id] = room
        return room

    def get_room(self, room_id: str) -> Room:
        room = self._rooms.get(room_id)
        if room is None:
            raise RoomNotFoundError(room_id)
        return room

    def add_participant(self, room_id: str, participant_id: str, password: str | None = None) -> Room:
        room = self.get_room(room_id)
        if room.locked:
            raise RoomLockedError(room_id)
        if not room.verify_password(password):
            raise WrongPasswordError(room_id)
        if room.participant_count >= self._max_participants_per_room:
            raise RoomFullError(room_id)
        room.participants[participant_id] = Participant(participant_id=participant_id)
        return room

    def set_room_locked(self, room_id: str, requester_id: str, *, locked: bool) -> Room:
        room = self.get_room(room_id)
        if room.host_id != requester_id:
            raise NotHostError(room_id)
        room.locked = locked
        return room

    def remove_participant(self, room_id: str, participant_id: str) -> Room:
        room = self.get_room(room_id)
        room.participants.pop(participant_id, None)
        return room

    def remove_room(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)

    def has_room(self, room_id: str) -> bool:
        return room_id in self._rooms

    @property
    def room_count(self) -> int:
        return len(self._rooms)
