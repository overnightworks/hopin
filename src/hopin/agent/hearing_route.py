"""The hearing route: a sponsor's browser sends its microphone sound through HopIn's server to the speech service, and
each finished sentence enters the window of the agent that hears the seat.

HopIn keeps the route, the admission, the choice of tokens and when a hop ends; the speech client's relay and
finished-utterance helper keep the sound's relay and the service's session. The browser gets no token and no text, only
the code its socket closes with (`HearingClose`), and nothing here logs sound, text or a token."""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, assert_never

import structlog
from speech_client.hearing import Closed, Refused
from speech_client.outcomes import Busy, Cut, Malformed, OverCap, PastDeadline, Unreachable
from speech_client.relay import HOP_FRAME_BYTES, AppEnded, BrowserLeft, RuleBroken
from speech_client.utterances import ServiceFailed, Utterance, UtteranceOutcome, hear_utterances
from speech_client.wire import Language
from starlette.websockets import WebSocket  # noqa: TC002 - FastAPI resolves the route annotation at runtime
from webauth.oidc import OidcRefusal

from hopin.agent.issuer_error import issuer_error
from hopin.config.constants import POLICY_VIOLATION_CLOSE_CODE, SESSION_RECHECK_SECONDS
from hopin.participation.feature import Person
from hopin.participation.vocabulary import HearingClose, HearingRefusal
from hopin.signaling.frames import PeerId

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr
    from webauth.oidc import OidcRelyingParty

    from hopin.agent.hearing_token import HearingTokens
    from hopin.agent.speech_settings import SpeechService
    from hopin.participation.feature import Participation
    from hopin.participation.hearing import HearingHandle
    from hopin.signaling.identity import IdentityResolver
    from hopin.signaling.scheduler import Scheduler, TimerHandle

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# A guest may speak English in a German call; the service decodes each sentence in the language it hears.
HEARING_LANGUAGES: Final = (Language.DE, Language.EN)
HEARING_DEADLINE_SECONDS: Final = 60 * 60
# Audio the relay accepted and the service has not read may still be written to the service after a hop ends, up to
# this many bytes (speech#311); a few frames keep that tail short.
HELD_FRAMES: Final = 5
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
    """Serves the hearing socket of a seat: admission, the seat's hop, the two tokens and the speech session."""

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
        handle = self._participation().open_hearing(Person(self._issuer, account.id), PeerId(seat))
        if isinstance(handle, HearingRefusal):
            await _close_unopened(websocket, HearingClose[handle.name])
            return
        person_token = self._tokens.held(handle.agent)
        if person_token is None:
            await _close_unopened(websocket, HearingClose.NO_TOKEN)
            return
        # The hop ends when its record ends it or its session is revoked; both set `handle.ended`, watched from the
        # grant on.
        recheck = _SessionRecheck(self._resolver, self._scheduler, websocket, handle.ended)
        try:
            await self._hear(websocket, handle, person_token)
        finally:
            recheck.close()

    async def _hear(self, websocket: WebSocket, handle: HearingHandle, person_token: SecretStr) -> None:
        workload = await self._workload_token_unless(handle.ended)
        if workload is None:
            _log_ended(AppEnded())
            await websocket.close(code=HearingClose.NORMAL)
            return
        if isinstance(workload, OidcRefusal):
            logger.info("agent_hearing_workload_token_refused", idp_error=issuer_error(workload))
            await _close_unopened(websocket, HearingClose.SERVICE_FAILED)
            return
        clock = _AudioClock(self._scheduler.now())

        async def on_utterance(utterance: Utterance) -> None:
            self._participation().heard(handle.seat, utterance.text, clock.began_at(utterance))

        outcome = await hear_utterances(
            websocket,
            self._service.origin,
            workload_token=workload.get_secret_value(),
            person_token=person_token.get_secret_value(),
            languages=HEARING_LANGUAGES,
            deadline_seconds=HEARING_DEADLINE_SECONDS,
            held_limit_bytes=HELD_LIMIT_BYTES,
            ended=handle.ended,
            on_utterance=on_utterance,
        )
        _log_ended(outcome)

    async def _workload_token_unless(self, ended: asyncio.Event) -> SecretStr | OidcRefusal | None:
        """HopIn's own token for the service, or none when the hop ended while the issuer was still answering."""
        grant = asyncio.ensure_future(
            asyncio.to_thread(self._relying_party.workload_access_token, scopes=(self._service.audience_scope,))
        )
        stopped = asyncio.ensure_future(ended.wait())
        try:
            await asyncio.wait({grant, stopped}, return_when=asyncio.FIRST_COMPLETED)
            return None if ended.is_set() else grant.result()
        finally:
            grant.cancel()
            stopped.cancel()


async def _close_unopened(websocket: WebSocket, code: HearingClose) -> None:
    logger.info("agent_hearing_refused", close=code.name.lower())
    await websocket.close(code=code)


def _log_ended(outcome: UtteranceOutcome) -> None:
    ended, code = _ending_of(outcome)
    # Only closed codes: the sound, the text and a token never reach a log.
    logger.info("agent_hearing_ended", outcome=ended, **({} if code is None else {"code": code}))


def _ending_of(outcome: UtteranceOutcome) -> tuple[str, str | None]:
    match outcome:
        case Closed():
            return "closed", None
        case Refused(code=refusal_code):
            return "refused", None if refusal_code is None else refusal_code.value
        case ServiceFailed(code=failure_code):
            return "service_failed", failure_code.value
        case Busy():
            return "busy", None
        case OverCap():
            return "over_cap", None
        case Malformed():
            return "malformed", None
        case PastDeadline():
            return "past_deadline", None
        case Cut():
            return "cut", None
        case Unreachable():
            return "unreachable", None
        case BrowserLeft():
            return "browser_left", None
        case AppEnded():
            return "app_ended", None
        case RuleBroken(close=hop_close):
            return "rule_broken", hop_close.name.lower()
        case _:  # pragma: no cover - the type checker proves the match exhaustive
            assert_never(outcome)
