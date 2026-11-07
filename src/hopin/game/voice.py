import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from fastapi import APIRouter, HTTPException, Response

from hopin.game.vocabulary import VOICE_PATH
from hopin.web.routes import READ_METHODS

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

ID_BYTES: Final = 16
WAV_MEDIA_TYPE: Final = "audio/wav"


class Voice(Protocol):
    async def speak(self, starter: HTTPConnection, text: str) -> bytes | None:
        """The WAV of the text spoken on the starter's access, or nothing for any reason.

        Raises nothing but `CancelledError`; a cancelled call's worker ends within `VOICE_DEADLINE_SECONDS`.
        """


@dataclass(frozen=True)
class _Stored:
    room_id: str
    wav: bytes


class VoiceStore:
    """Each room's latest voice, fetched by an unguessable id until the room's next voice or its end."""

    def __init__(self) -> None:
        self._by_id: dict[str, _Stored] = {}
        self._id_of_room: dict[str, str] = {}

    def put(self, room_id: str, wav: bytes) -> str:
        self.forget_room(room_id)
        voice_id = secrets.token_urlsafe(ID_BYTES)
        self._by_id[voice_id] = _Stored(room_id, wav)
        self._id_of_room[room_id] = voice_id
        return voice_id

    def get(self, voice_id: str) -> bytes | None:
        stored = self._by_id.get(voice_id)
        return None if stored is None else stored.wav

    def forget_room(self, room_id: str) -> None:
        voice_id = self._id_of_room.pop(room_id, None)
        if voice_id is not None:
            del self._by_id[voice_id]


def create_voice_router(store: VoiceStore) -> APIRouter:
    router = APIRouter()

    @router.api_route(f"{VOICE_PATH}/{{voice_id}}", methods=READ_METHODS)
    def voice(voice_id: str) -> Response:
        wav = store.get(voice_id)
        if wav is None:
            raise HTTPException(status_code=404)
        return Response(wav, media_type=WAV_MEDIA_TYPE, headers={"Cache-Control": "no-store"})

    return router
