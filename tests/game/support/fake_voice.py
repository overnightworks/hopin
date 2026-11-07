import asyncio
from collections import deque
from dataclasses import dataclass, field

DEFAULT_WAV = b"RIFF-the-fake-voice"


@dataclass(eq=False)
class HeldSpeech:
    """One held call of the fake voice: it answers once released."""

    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(eq=False)
class SpeakCall:
    starter: object
    text: str
    cancelled: bool = False
    finished: bool = False


class FakeVoice:
    """Answers each call with the next scripted WAV, nothing or an exception; a call by its text can be held."""

    def __init__(self) -> None:
        self._answers: deque = deque()
        self._holds: dict[str, HeldSpeech] = {}
        self.calls: list[SpeakCall] = []

    def answer_next(self, *answers) -> None:
        self._answers.extend(answers)

    def hold(self, text: str) -> HeldSpeech:
        """Hold the call that speaks this text until it is released."""
        held = HeldSpeech()
        self._holds[text] = held
        return held

    def release_every_hold(self) -> None:
        for held in self._holds.values():
            held.release.set()

    async def speak(self, starter, text):
        call = SpeakCall(starter, text)
        self.calls.append(call)
        held = self._holds.get(text)
        try:
            if held is not None:
                held.started.set()
                await held.release.wait()
            answer = self._answers.popleft() if self._answers else DEFAULT_WAV
            if isinstance(answer, BaseException):
                raise answer
            return answer
        except asyncio.CancelledError:
            call.cancelled = True
            raise
        finally:
            call.finished = True
