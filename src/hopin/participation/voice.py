"""The sounds a call's seats fetch: each is kept under an unguessable id while it waits or plays, grows as its chunks
arrive and is served to the seats of its room only."""

import asyncio
import secrets
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from fastapi import HTTPException, Request, Response
from starlette.responses import StreamingResponse
from webauth.middleware import admitted_user

from hopin.participation.vocabulary import VOICE_PATH

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

ID_BYTES: Final = 16
WAV_MEDIA_TYPE: Final = "audio/wav"


class VoiceBrokenError(Exception):
    """A sound's stream failed before the whole of it was kept."""


class StoredVoice:
    """A kept sound: its id, the path it is fetched at and the chunks held so far. A reader gets them from the start
    and then each one as it arrives, until the sound is finished, which ends the reader, or broken, which fails it."""

    def __init__(self, voice_id: str) -> None:
        self.id = voice_id
        self.path = f"{VOICE_PATH}/{voice_id}"
        self._chunks: list[bytes] = []
        self._finished = False
        self._broken = False
        self._changed = asyncio.Event()

    def append(self, chunk: bytes) -> None:
        if not self._finished and not self._broken:
            self._chunks.append(chunk)
            self._wake_readers()

    def finish(self) -> None:
        """The sound ends here: whole, or dropped, which stops every reader without a failure, unless it broke off."""
        if not self._broken:
            self._finished = True
            self._wake_readers()

    def break_off(self) -> None:
        """The sound's stream failed: every reader fails where it stands, unless the sound ended."""
        if not self._finished:
            self._broken = True
            self._wake_readers()

    async def read(self) -> AsyncIterator[bytes]:
        """Every chunk from the first, each as it arrives; raises `VoiceBrokenError` where the sound broke off."""
        position = 0
        while True:
            while position < len(self._chunks):
                yield self._chunks[position]
                position += 1
            if self._finished:
                return
            if self._broken:
                raise VoiceBrokenError
            await self._changed.wait()

    def _wake_readers(self) -> None:
        self._changed.set()
        self._changed = asyncio.Event()


@dataclass(frozen=True)
class _Stored:
    room_id: str
    speaker: str
    voice: StoredVoice


class VoiceStore:
    """The sounds of a room's speakers, in memory only, fetched by an unguessable id until they are released or their
    speaker or room ends."""

    def __init__(self) -> None:
        self._by_id: dict[str, _Stored] = {}
        self._ids_of_speaker: defaultdict[tuple[str, str], list[str]] = defaultdict(list)

    def open(self, room_id: str, speaker: str) -> StoredVoice:
        """Keep a sound under a new id, empty and unfinished, for its writer to fill."""
        voice = StoredVoice(secrets.token_urlsafe(ID_BYTES))
        self._by_id[voice.id] = _Stored(room_id, speaker, voice)
        self._ids_of_speaker[room_id, speaker].append(voice.id)
        return voice

    def put(self, room_id: str, speaker: str, wav: bytes) -> StoredVoice:
        """Keep a whole sound under a new id."""
        voice = self.open(room_id, speaker)
        voice.append(wav)
        voice.finish()
        return voice

    def get(self, voice_id: str) -> StoredVoice | None:
        stored = self._by_id.get(voice_id)
        return None if stored is None else stored.voice

    def room_of(self, voice_id: str) -> str | None:
        stored = self._by_id.get(voice_id)
        return None if stored is None else stored.room_id

    def release(self, voice_id: str) -> None:
        """Drop the sound: no one fetches it again, and a reader of it ends where the sound stands."""
        stored = self._by_id.pop(voice_id)
        stored.voice.finish()
        held = self._ids_of_speaker[stored.room_id, stored.speaker]
        held.remove(voice_id)
        if not held:
            del self._ids_of_speaker[stored.room_id, stored.speaker]

    def keep_latest(self, room_id: str, speaker: str, count: int) -> None:
        """Release the speaker's sounds in the room but the `count` it kept last."""
        held = self._ids_of_speaker.get((room_id, speaker), [])
        for voice_id in held[: max(len(held) - count, 0)]:
            self.release(voice_id)

    def forget_speaker(self, room_id: str, speaker: str) -> None:
        for voice_id in self._ids_of_speaker.pop((room_id, speaker), []):
            self._by_id.pop(voice_id).voice.finish()


def voice_endpoint(
    store: VoiceStore, holds_seat: Callable[[str, str], bool]
) -> Callable[[Request, str], Awaitable[Response]]:
    """The route of a sound, at `VOICE_PATH` and the voice's id: a signed-in account that holds a seat in the sound's
    room gets it from its start as it grows, no one else."""

    async def voice(request: Request, voice_id: str) -> Response:  # NOSONAR(S7503) reads state the event loop owns
        user = admitted_user(request)
        stored = store.get(voice_id)
        room_id = store.room_of(voice_id)
        if user is None or stored is None or room_id is None or not holds_seat(user.id, room_id):
            raise HTTPException(status_code=404)
        return StreamingResponse(stored.read(), media_type=WAV_MEDIA_TYPE, headers={"Cache-Control": "no-store"})

    return voice
