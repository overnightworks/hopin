from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

# The game keeps its lines' voices under this speaker.
GAME_SPEAKER: Final = "game"

# The latest voices kept: a fast speech service makes the next line's voice within milliseconds of sending a voice's
# frame, before a seat's fetch of that voice arrives, so the voice before the latest stays fetchable.
GAME_VOICES_KEPT: Final = 2

# The message of the cancel that cuts a voice at its deadline, so the voice can tell it from the end of its run.
VOICE_DEADLINE_CUT: Final = "voice_deadline"


class Voice(Protocol):
    async def speak(self, starter: HTTPConnection, text: str) -> bytes | None:
        """The WAV of the text spoken on the starter's access, or nothing for any reason.

        Raises nothing but `CancelledError`; a cancelled call's worker ends within `VOICE_DEADLINE_SECONDS`. A call
        cancelled with `VOICE_DEADLINE_CUT` may end in nothing instead, after ending its step as past its deadline.
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

    def keep_latest(self, room_id: str, speaker: str, count: int) -> None:
        """Drop the speaker's sounds in the room but the `count` it kept last."""

    def forget_speaker(self, room_id: str, speaker: str) -> None:
        """Drop every sound the speaker has kept in the room."""
