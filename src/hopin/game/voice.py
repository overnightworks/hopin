from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

# The game keeps its lines' voices under this speaker, one at a time.
GAME_SPEAKER: Final = "game"


class Voice(Protocol):
    async def speak(self, starter: HTTPConnection, text: str) -> bytes | None:
        """The WAV of the text spoken on the starter's access, or nothing for any reason.

        Raises nothing but `CancelledError`; a cancelled call's worker ends within `VOICE_DEADLINE_SECONDS`.
        """


class StoredVoice(Protocol):
    """A kept sound: its id and the path a seat fetches it at."""

    @property
    def id(self) -> str:
        """The voice's id."""

    @property
    def path(self) -> str:
        """The path a seat fetches the voice at."""


class VoiceShelf(Protocol):
    """Where the sounds of a room's speakers are kept for its seats to fetch."""

    def put(self, room_id: str, speaker: str, wav: bytes) -> StoredVoice:
        """Keep the sound under a new id."""

    def forget_speaker(self, room_id: str, speaker: str) -> None:
        """Drop every sound the speaker has kept in the room."""
