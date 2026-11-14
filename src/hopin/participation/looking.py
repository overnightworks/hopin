"""A look is one still picture of a sponsor's camera, asked of the sponsor's browser by a request id. The browser
answers in parts, each within a signaling frame, or with none; the picture is assembled in memory only while the
request waits, and is handed to the asker or dropped. No part and no picture is logged."""

import asyncio
import base64
import binascii
import secrets
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final

from hopin.errors import InvalidMessageError
from hopin.participation.jpeg import size_of
from hopin.participation.vocabulary import (
    LOOK_ANSWER_SECONDS,
    LOOK_MAX_BYTES,
    LOOK_MAX_PARTS,
    LOOK_MAX_PIXELS,
    LOOK_PART_CHARACTERS,
    LookRefusal,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hopin.participation.participants import DialedAgent
    from hopin.signaling.frames import ForeignFrame, PeerId
    from hopin.signaling.scheduler import Scheduler, TimerHandle

REQUEST_ID_BYTES: Final = 16

type Picture = bytes | LookRefusal
"""The JPEG a look yields, or why it yields none."""


@dataclass
class _Request:
    """A look that waits for its answer: the agent that asks, and the parts of the picture that came so far."""

    agent: DialedAgent
    answer: asyncio.Future[Picture] = field(repr=False)
    parts: dict[int, str] = field(default_factory=dict, repr=False)
    count: int | None = None
    timer: TimerHandle | None = None


@dataclass(frozen=True)
class _Part:
    """One part of a picture's base64 text: its place among `count` parts."""

    index: int
    count: int
    data: str = field(repr=False)


def _part_of(frame: ForeignFrame) -> _Part:
    index, count, data = (frame.data.get(name) for name in ("index", "count", "data"))
    if not (isinstance(index, int) and isinstance(count, int) and isinstance(data, str)):
        raise InvalidMessageError("A look part needs its index, the count of parts and its data")
    return _Part(index, count, data)


class Looking:
    """The looks that wait for their sponsor's browser. `ask` sends the request to the sponsor's seat."""

    def __init__(self, scheduler: Scheduler, ask: Callable[[DialedAgent, str], Awaitable[None]]) -> None:
        self._scheduler = scheduler
        self._ask = ask
        self._requests: dict[str, _Request] = {}

    async def look(self, agent: DialedAgent) -> Picture:
        """Ask the agent's sponsor for a picture and wait for the browser's answer, which the sponsor's leave or an end
        of the agent also gives, or for the time to run out."""
        request_id = secrets.token_hex(REQUEST_ID_BYTES)
        request = _Request(agent, asyncio.get_running_loop().create_future())
        self._requests[request_id] = request
        request.timer = self._scheduler.call_later(LOOK_ANSWER_SECONDS, partial(self._timed_out, request))
        try:
            await self._ask(agent, request_id)
            return await request.answer
        finally:
            request.timer.cancel()
            del self._requests[request_id]

    def answered(self, sender: PeerId, frame: ForeignFrame, *, part: bool) -> None:
        """Take a sponsor's answer, a part of the picture or the word that there is none; an answer to no waiting
        request, or from a seat that is not the agent's sponsor, is dropped. A part that breaks the bounds ends the
        request as unavailable."""
        request_id = frame.data.get("request_id")
        if not isinstance(request_id, str):
            raise InvalidMessageError("A look answer needs the request's id")
        taken = _part_of(frame) if part else None
        request = self._requests.get(request_id)
        if request is None or request.agent.sponsor != sender:
            return
        if taken is None:
            _answer(request, LookRefusal.NO_CAMERA)
        else:
            self._take_part(request, taken)

    def end(self, agent: DialedAgent, refusal: LookRefusal) -> None:
        """The agent's looks that wait get no picture: it ended, or its sponsor turned its seeing off."""
        for request in self._requests.values():
            if request.agent.agent_id == agent.agent_id:
                _answer(request, refusal)

    async def _timed_out(self, request: _Request) -> None:  # NOSONAR(S7503) a scheduler callback is async
        _answer(request, LookRefusal.NO_ANSWER)

    def _take_part(self, request: _Request, taken: _Part) -> None:
        if request.count not in (None, taken.count) or not 0 <= taken.index < taken.count <= LOOK_MAX_PARTS:
            _answer(request, LookRefusal.UNAVAILABLE)
            return
        if len(taken.data) > LOOK_PART_CHARACTERS or taken.index in request.parts:
            _answer(request, LookRefusal.UNAVAILABLE)
            return
        request.count = taken.count
        request.parts[taken.index] = taken.data
        if len(request.parts) == taken.count:
            _answer(request, _picture_of("".join(request.parts[position] for position in range(taken.count))))


def _answer(request: _Request, picture: Picture) -> None:
    if not request.answer.done():
        request.answer.set_result(picture)


def _picture_of(encoded: str) -> Picture:
    """The JPEG the base64 text carries when it is within the bounds of a look, else why there is none."""
    try:
        picture = base64.b64decode(encoded, validate=True)
    except binascii.Error, ValueError:
        return LookRefusal.UNAVAILABLE
    size = size_of(picture)
    if size is None or len(picture) > LOOK_MAX_BYTES or not all(0 < side <= LOOK_MAX_PIXELS for side in size):
        return LookRefusal.UNAVAILABLE
    return picture
