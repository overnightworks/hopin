import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from fastapi import APIRouter, HTTPException, Response

from hopin.game.vocabulary import VOICE_PATH
from hopin.web.routes import READ_METHODS

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

ID_BYTES: Final = 16
VOICE_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{22}")
WAV_MEDIA_TYPE: Final = "audio/wav"


class Voice(Protocol):
    async def speak(self, starter: HTTPConnection, text: str) -> bytes | None:
        """The WAV of the text spoken on the starter's access, or nothing for any reason.

        Raises nothing but `CancelledError`; a cancelled call has ended its work when it ends.
        """


@dataclass(frozen=True)
class _Stored:
    voice_id: str
    wav: bytes


class VoiceStore:
    """Each room's latest voice, fetched by an unguessable id until the room's next voice or its end."""

    def __init__(self) -> None:
        self._by_room: dict[str, _Stored] = {}

    def put(self, room_id: str, wav: bytes) -> str:
        voice_id = secrets.token_urlsafe(ID_BYTES)
        self._by_room[room_id] = _Stored(voice_id, wav)
        return voice_id

    def get(self, voice_id: str) -> bytes | None:
        return next((stored.wav for stored in self._by_room.values() if stored.voice_id == voice_id), None)

    def forget_room(self, room_id: str) -> None:
        self._by_room.pop(room_id, None)


def create_voice_router(store: VoiceStore) -> APIRouter:
    router = APIRouter()

    @router.api_route(f"{VOICE_PATH}/{{voice_id}}", methods=READ_METHODS)
    def voice(voice_id: str) -> Response:
        wav = store.get(voice_id) if VOICE_ID_PATTERN.fullmatch(voice_id) else None
        if wav is None:
            raise HTTPException(status_code=404)
        return Response(wav, media_type=WAV_MEDIA_TYPE, headers={"Cache-Control": "no-store"})

    return router
