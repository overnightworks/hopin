"""The sounds a call's seats fetch: each is kept under an unguessable id while it waits or plays and is served to the
seats of its room only."""

import secrets
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastapi import HTTPException, Request, Response
from webauth.middleware import admitted_user

from hopin.participation.vocabulary import VOICE_PATH

if TYPE_CHECKING:
    from collections.abc import Callable

ID_BYTES: Final = 16
WAV_MEDIA_TYPE: Final = "audio/wav"


@dataclass(frozen=True)
class StoredVoice:
    """A kept sound's id and the path it is fetched at."""

    id: str
    path: str


@dataclass(frozen=True)
class _Stored:
    room_id: str
    speaker: str
    wav: bytes


class VoiceStore:
    """The sounds of a room's speakers, in memory only, fetched by an unguessable id until they are released or their
    speaker or room ends."""

    def __init__(self) -> None:
        self._by_id: dict[str, _Stored] = {}
        self._ids_of_speaker: defaultdict[tuple[str, str], set[str]] = defaultdict(set)

    def put(self, room_id: str, speaker: str, wav: bytes) -> StoredVoice:
        voice_id = secrets.token_urlsafe(ID_BYTES)
        self._by_id[voice_id] = _Stored(room_id, speaker, wav)
        self._ids_of_speaker[room_id, speaker].add(voice_id)
        return StoredVoice(voice_id, f"{VOICE_PATH}/{voice_id}")

    def get(self, voice_id: str) -> bytes | None:
        stored = self._by_id.get(voice_id)
        return None if stored is None else stored.wav

    def room_of(self, voice_id: str) -> str | None:
        stored = self._by_id.get(voice_id)
        return None if stored is None else stored.room_id

    def release(self, voice_id: str) -> None:
        stored = self._by_id.pop(voice_id)
        held = self._ids_of_speaker[stored.room_id, stored.speaker]
        held.discard(voice_id)
        if not held:
            del self._ids_of_speaker[stored.room_id, stored.speaker]

    def forget_speaker(self, room_id: str, speaker: str) -> None:
        for voice_id in self._ids_of_speaker.pop((room_id, speaker), set()):
            del self._by_id[voice_id]


def voice_endpoint(store: VoiceStore, holds_seat: Callable[[str, str], bool]) -> Callable[[Request, str], Response]:
    """The route of a sound, at `VOICE_PATH` and the voice's id: a signed-in account that holds a seat in the sound's
    room gets it, no one else."""

    def voice(request: Request, voice_id: str) -> Response:
        user = admitted_user(request)
        wav = store.get(voice_id)
        room_id = store.room_of(voice_id)
        if user is None or wav is None or room_id is None or not holds_seat(user.id, room_id):
            raise HTTPException(status_code=404)
        return Response(wav, media_type=WAV_MEDIA_TYPE, headers={"Cache-Control": "no-store"})

    return voice
