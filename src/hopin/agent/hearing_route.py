"""The hearing route: a sponsor's browser sends its microphone sound through HopIn's server to the speech service, and
each finished sentence enters the window of the agent that hears the seat.

HopIn keeps the route, the admission, the choice of tokens and when a hop ends; the speech client's relay and
finished-utterance helper keep the sound's relay and the service's session. The browser gets no token and no text, only
the code its socket closes with (`HearingClose`), and nothing here logs sound, text or a token."""

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, assert_never

import structlog
from logline import StepTimer, unit_trace
from speech_client.hearing import NEEDED_HOLD_BYTES, Closed, NoToken, ReconnectFailed, Refused, SessionTokens
from speech_client.outcomes import Busy, Cut, Malformed, OverCap, PastDeadline, Unreachable
from speech_client.relay import HOP_FRAME_BYTES, AppEnded, BrowserLeft, RuleBroken
from speech_client.utterances import (
    HearingCallbacks,
    Partial,
    ServiceFailed,
    SpeechStart,
    TurnEnd,
    Utterance,
    UtteranceOutcome,
    hear_utterances,
)
from speech_client.wire import Language
from starlette.websockets import WebSocket  # noqa: TC002 - FastAPI resolves the route annotation at runtime
from webauth.oidc import OidcRefusal

from hopin.agent.workload_token import workload_token
from hopin.config.constants import POLICY_VIOLATION_CLOSE_CODE, SESSION_RECHECK_SECONDS
from hopin.participation.participants import Person
from hopin.participation.vocabulary import HearingClose, HearingRefusal
from hopin.rooms.reference import room_reference
from hopin.signaling.frames import PeerId

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic import SecretStr
    from webauth.oidc import OidcRelyingParty

    from hopin.agent.hearing_token import HearingTokens
    from hopin.agent.speech_settings import SpeechService
    from hopin.participation.feature import Participation
    from hopin.participation.hearing import HearingHandle
    from hopin.signaling.identity import IdentityResolver
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()
_STEPS: Final = logging.getLogger(__name__)

# A guest may speak English in a German call; the service decodes each sentence in the language it hears.
HEARING_LANGUAGES: Final = (Language.DE, Language.EN)
HEARING_DEADLINE_SECONDS: Final = 60 * 60
# The client holds the sound it is sent until the service's session is open and reads it, and waits up to its opening
# bound for that opening, or its reconnect bound while it reopens a drained or cut session; a smaller hold ends the hop
# with `OverCap` on a session that opens slowly or stalls for a moment, and the person goes unheard. So the hold is the
# client's own `NEEDED_HOLD_BYTES`, which covers both bounds. Audio held when a hop ends may
# still be written to the service (speech#311): sound spoken while the switch was on, and CALL-58 keeps any sentence
# that ends after a switch-off out of every window.
HELD_FRAMES: Final = math.ceil(NEEDED_HOLD_BYTES / HOP_FRAME_BYTES)
HELD_LIMIT_BYTES: Final = HOP_FRAME_BYTES * HELD_FRAMES
MILLISECONDS_PER_SECOND: Final = 1000


@dataclass
class _AudioClock:
    """Maps a position in a hop's audio to the scheduler's clock, the clock the record of who hears whom reads: the
    hop's audio starts no earlier than the moment the route opened it. A sentence without a start position begins where
    the previous one ended."""

    opened_at: float
    previous_end: float = field(init=False)

    def __post_init__(self) -> None:
        self.previous_end = self.opened_at

    def began_at(self, utterance: Utterance) -> float:
        began = self.previous_end if utterance.audio_start_ms is None else self._at(utterance.audio_start_ms)
        self.previous_end = began if utterance.audio_end_ms is None else self._at(utterance.audio_end_ms)
        return began

    def _at(self, position_ms: int) -> float:
        return self.opened_at + position_ms / MILLISECONDS_PER_SECOND


class _SessionRecheck:
    """Asks each minute whether the socket's session still admits it, and sets the hop's `ended` once it does not or
    the check fails: sound a revoked session sent is not relayed on. It asks no more once closed."""

    def __init__(
        self, resolver: IdentityResolver, scheduler: Scheduler, websocket: WebSocket, ended: asyncio.Event
    ) -> None:
        self._resolver = resolver
        self._scheduler = scheduler
        self._websocket = websocket
        self._ended = ended
        self._closed = False
        self._timer: TimerHandle = self._schedule()

    def close(self) -> None:
        # A check that is running is not interrupted by the cancel, so it also finds `_closed` when it returns.
        self._closed = True
        self._timer.cancel()

    def _schedule(self) -> TimerHandle:
        return self._scheduler.call_later(SESSION_RECHECK_SECONDS, self._tick)

    async def _tick(self) -> None:
        if self._closed or self._ended.is_set():
            return
        try:
            account = await self._resolver.resolve(self._websocket)
        except Exception:
            self._ended.set()
            raise
        if self._closed:
            return
        if account is None:
            self._ended.set()
        else:
            self._timer = self._schedule()


class HearingRoute:
    """Serves the hearing socket of a seat: admission, the seat's hop, each opening's tokens, the speech session."""

    def __init__(
        self,
        *,
        service: SpeechService,
        issuer: str,
        resolver: IdentityResolver,
        participation: Callable[[], Participation],
        tokens: HearingTokens,
        relying_party: OidcRelyingParty,
        scheduler: Scheduler,
    ) -> None:
        self._service = service
        self._issuer = issuer
        self._resolver = resolver
        self._participation = participation
        self._tokens = tokens
        self._relying_party = relying_party
        self._scheduler = scheduler

    async def serve(self, websocket: WebSocket, seat: str) -> None:
        account = await self._resolver.resolve(websocket)
        if account is None:
            await websocket.close(code=POLICY_VIOLATION_CLOSE_CODE)
            return
        await websocket.accept()
        handle = self._participation().open_hearing(
            Person(self._issuer, account.id), PeerId(seat), lambda agent: self._tokens.held(agent) is not None
        )
        if isinstance(handle, HearingRefusal):
            await _close_unopened(websocket, HearingClose[handle.name])
            return
        if self._tokens.held(handle.agent) is None:
            await _close_unopened(websocket, HearingClose.NO_TOKEN)
            return
        # The hop ends when its record ends it or its session is revoked; both set `handle.ended`, watched from the
        # grant on.
        recheck = _SessionRecheck(self._resolver, self._scheduler, websocket, handle.ended)
        try:
            # The hop's own lines and the session's upgrade to the service share one trace, apart from each turn's.
            with unit_trace(None):
                await self._hear(websocket, handle)
        finally:
            recheck.close()
            self._participation().speech.turn_ended(handle.room_id, handle.seat)

    async def _tokens_for_opening(self, handle: HearingHandle) -> SessionTokens | None:
        """The tokens of one opening of the service's session, the first and each reopening: the agent's held token as
        it stands now and HopIn's workload token. None once the hop ended, the agent holds none or the issuer
        refuses."""
        person_token = self._tokens.held(handle.agent)
        if person_token is None:
            return None
        workload = await self._workload_token_unless(handle.ended)
        if workload is None or isinstance(workload, OidcRefusal):
            return None
        return SessionTokens(workload_token=workload.get_secret_value(), person_token=person_token.get_secret_value())

    async def _hear(self, websocket: WebSocket, handle: HearingHandle) -> None:
        clock = _AudioClock(self._scheduler.now())

        def on_utterance(utterance: Utterance) -> Awaitable[None]:
            # The turn runs in the trace the service names, else starts one here: the socket's task outlives the turn.
            with (
                unit_trace(utterance.traceparent),
                StepTimer("transcript_relay.ended", _STEPS, room_ref=room_reference(handle.room_id)),
            ):
                self._participation().heard(handle.seat, utterance.text, clock.began_at(utterance), utterance.item_id)
            return asyncio.sleep(0)  # the client awaits its callback; hearing is synchronous

        def on_speech_started(_: SpeechStart) -> Awaitable[None]:
            self._participation().speech.person_started(handle.room_id, handle.seat)
            return asyncio.sleep(0)

        def on_partial(partial: Partial) -> Awaitable[None]:
            self._participation().speech.words_heard(handle.room_id, handle.seat, partial.text)
            return asyncio.sleep(0)

        def on_turn_ended(_: TurnEnd) -> Awaitable[None]:
            self._participation().speech.turn_ended(handle.room_id, handle.seat)
            return asyncio.sleep(0)

        async with StepTimer(
            "hearing.ended", _STEPS, room_ref=room_reference(handle.room_id), peer_id=handle.seat
        ) as step:
            outcome = await hear_utterances(
                websocket,
                self._service.origin,
                token_source=lambda: self._tokens_for_opening(handle),
                languages=HEARING_LANGUAGES,
                deadline_seconds=HEARING_DEADLINE_SECONDS,
                held_limit_bytes=HELD_LIMIT_BYTES,
                ended=handle.ended,
                callbacks=HearingCallbacks(
                    on_utterance=on_utterance,
                    on_partial=on_partial,
                    on_speech_started=on_speech_started,
                    on_turn_ended=on_turn_ended,
                ),
            )
            reason = ending_reason(outcome)
            if reason is not None:
                step.refuse(reason)

    async def _workload_token_unless(self, ended: asyncio.Event) -> SecretStr | OidcRefusal | None:
        """HopIn's own token for the service, or none when the hop ended while the issuer was still answering."""
        grant = asyncio.ensure_future(workload_token(self._relying_party, self._service.audience_scope))
        stopped = asyncio.ensure_future(ended.wait())
        try:
            await asyncio.wait({grant, stopped}, return_when=asyncio.FIRST_COMPLETED)
            return None if ended.is_set() else grant.result()
        finally:
            grant.cancel()
            stopped.cancel()


async def _close_unopened(websocket: WebSocket, code: HearingClose) -> None:
    logger.info("hearing.refused", reason=code.name.lower())
    await websocket.close(code=code)


def ending_reason(outcome: UtteranceOutcome) -> str | None:
    """Why a hop ended in a way that served nobody, a code from a closed set: the service's refusal or failure code, the
    rule the browser broke, or the ending's own name. Nothing for a hop that ended as designed, because it closed, the
    browser left or the app ended it."""
    match outcome:
        case Refused(code=refusal_code):
            return "refused" if refusal_code is None else refusal_code.value
        case ServiceFailed(code=failure_code):
            return failure_code.value
        case RuleBroken(close=hop_close):
            return hop_close.name.lower()
        case Closed() | BrowserLeft() | AppEnded():
            return None
        case Busy():
            return "busy"
        case OverCap():
            return "over_cap"
        case Malformed():
            return "malformed"
        case PastDeadline():
            return "past_deadline"
        case Cut():
            return "cut"
        case Unreachable():
            return "unreachable"
        case NoToken():
            return "no_token"
        case ReconnectFailed():
            return "reconnect_failed"
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(outcome)
