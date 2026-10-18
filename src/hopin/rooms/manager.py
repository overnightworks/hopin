import secrets
import string

from hopin.config.constants import ROOM_ID_LENGTH
from hopin.errors import NotHostError, RoomFullError, RoomLockedError, RoomNotFoundError
from hopin.rooms.models import Account, Participant, Room


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

    def create_room(self, host_peer_id: str, host: Account) -> Room:
        room_id = self._generate_room_id()
        room = Room(room_id=room_id, host=host)
        room.participants[host_peer_id] = Participant(participant_id=host_peer_id, account=host)
        self._rooms[room_id] = room
        return room

    def get_room(self, room_id: str) -> Room:
        room = self._rooms.get(room_id)
        if room is None:
            raise RoomNotFoundError(room_id)
        return room

    def add_participant(self, room_id: str, participant_id: str, account: Account) -> Room:
        room = self.get_room(room_id)
        if room.locked and not room.is_hosted_by(account):
            raise RoomLockedError(room_id)
        if not room.has_free_seat_for(account, self._max_participants_per_room):
            raise RoomFullError(room_id)
        room.participants[participant_id] = Participant(participant_id=participant_id, account=account)
        return room

    def get_room_for_host(self, room_id: str, requester: Account) -> Room:
        room = self.get_room(room_id)
        if not room.is_hosted_by(requester):
            raise NotHostError(room_id)
        return room

    def set_room_locked(self, room_id: str, requester: Account, *, locked: bool) -> Room:
        room = self.get_room_for_host(room_id, requester)
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
