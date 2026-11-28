"""HopIn's MCP surface: the person front a person's own agent calls with a bearer token, and participation built to
serve it.

The front reads the kind of agent from the admitted request and serves an agent with an own account its standing as a
resource, signalling it on every change of a place. It runs inside the app's lifespan. Every admitted call of an own
account, the opening of a listen included, renews its lease. The chat window of a call's place is mapped to the
library's window and read as a resource by the principal whose agent it is. The figure, clip and persona room routes
answer any signed-in member the verified file of a digest a joined agent names: the first read fetches it through
`participation.figures`, and the file is kept in memory.
"""

import asyncio
import logging
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog
from agents.mcp_server import McpPersonFront, PersonRequest, PersonTool, PlaceStore, PlaceStoreError, ResourceChanges
from agents.mcp_server.hearing import MemberHearing
from agents.mcp_server.redis_place_store import RedisPlaceStore
from agents.mcp_wire import MCP_ENDPOINT_PATH
from agents.member import (
    ACCEPT_INVITATION_TOOL,
    GESTURE_TOOL,
    LEASE_SECONDS,
    SET_FRAMING_TOOL,
    STANDING_URI,
    Interrupted,
    Invitation,
    MemberStanding,
    PartialSpeech,
    PlaceEntry,
    PlaceRef,
    PlaceWindow,
    Standing,
    StandingState,
    TalkedOver,
    VoiceState,
    place_ref_from_window_uri,
    place_window_uri,
)
from agents.tools import JsonObject, Principal, ToolOutcome
from fastapi import APIRouter, HTTPException, Response
from logline import StepTimer
from starlette.requests import HTTPConnection
from starlette.routing import Route
from webauth.middleware import ProtectedResource, resource_person
from webauth.oidc_login import AccessTokenRefusal
from webauth.policies import AuthorizedParties
from webauth.session_groups import SessionGroups

from hopin.agent.hearing import MemberHearingRecord
from hopin.agent.hearing_route import HearingRoute
from hopin.agent.hearing_token import HearingTokens
from hopin.agent.issuer_error import grant_refusal_reason
from hopin.agent.speaking import SPEAK_DEADLINE_SECONDS, SPEAK_MAX_BYTES, SPEECH_STREAM, AgentVoice, SpeechRenderer
from hopin.agent.tools import (
    CALL_KIND,
    CallerTool,
    participation_tools,
    refusal_to_serve,
    refused,
    video_looks_of_accept,
)
from hopin.agent.video_session import VideoOpening
from hopin.config.constants import IMMUTABLE_ASSET_CACHE_CONTROL, READ_METHODS, REDIS_AGENTS_KEY_PREFIX
from hopin.participation.feature import Participation
from hopin.participation.figure_transport import get_figure_over_https
from hopin.participation.figures import (
    SERVED_FILE_KINDS,
    FigureSource,
    FigureStore,
    FigureTransport,
    NamedFigure,
    ServedFileKind,
    fetch_figure,
)
from hopin.participation.hearing import Hearing
from hopin.participation.participants import Caller, OwnAccount, Person
from hopin.participation.vocabulary import (
    HEARING_PATH,
    VOICE_PATH,
)
from hopin.participation.voice import VoiceStore, voice_endpoint
from hopin.signaling.identity import SessionIdentityResolver
from hopin.signaling.own_turns import OwnTurns

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Coroutine, Mapping

    from fastapi import FastAPI
    from redis.asyncio import Redis as AsyncRedis
    from starlette.types import ASGIApp, Receive, Scope, Send
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionTransaction

    from hopin.agent.speech_settings import SpeechService
    from hopin.agent.video_settings import VideoService
    from hopin.config.settings import RealmSettings
    from hopin.participation.chat_window import WindowRead, WindowTalkedOver
    from hopin.participation.places import PlaceStanding
    from hopin.signaling.features import TransitionFeature
    from hopin.signaling.scheduler import Deadlines, Scheduler
    from hopin.web.ice_servers import TurnRelay
    from hopin.web.page_build import PageBuild
    from hopin.web.request_language import RequestLanguage

logger: structlog.stdlib.BoundLogger = structlog.get_logger()
_STEPS: Final = logging.getLogger(__name__)

GLB_MEDIA_TYPE: Final = "model/gltf-binary"
# The tools offered only while the principal's agent shows a figure.
FIGURE_TOOLS: Final = frozenset({GESTURE_TOOL, SET_FRAMING_TOOL})
OFFERS_RECOMPUTE_TASK_NAME: Final = "agent-offers-recompute"
OFFERS_RECOMPUTE_FAILED_EVENT: Final = "agent_offers_recompute.failed"
PLACE_SIGNAL_TASK_NAME: Final = "agent-place-signal"
PLACE_SIGNAL_FAILED_EVENT: Final = "place_signal.failed"
LISTENERS_READ_FAILED_EVENT: Final = "listeners_read.failed"
# A start that waits on the first confirmed subscription ends before a deployment's 30 s grace for a stop runs out,
# because the server reads a stop only after its start.
PLACE_SUBSCRIPTION_WAIT_SECONDS: Final = 20.0
PLACE_SUBSCRIPTION_TIMED_OUT_EVENT: Final = "place_subscription_start.timed_out"


@dataclass
class _BuiltParticipation:
    """The participation the call's handler runs and the tools reach. Its resource changes tell the participation when
    an agent starts or stops listening on its standing; the place store carries them between HopIn's processes."""

    deadlines: Deadlines
    wall_clock: Callable[[], float]
    groups: SessionGroups
    figure_sources: tuple[FigureSource, ...]
    hears: bool
    voices: VoiceStore
    places: PlaceStore
    changes: ResourceChanges = field(init=False)
    participation: Participation = field(init=False)
    # Who listens to the standing, as the place store last said; empty while it cannot say, so no agent is offered.
    _standing_listeners: frozenset[Principal] = field(init=False, default=frozenset())
    _listeners_reads: int = field(init=False, default=0)
    _tasks: OwnTurns = field(init=False, default_factory=OwnTurns)

    def __post_init__(self) -> None:
        self.changes = ResourceChanges(
            self.places, listening_changed=self._listening_changed, listening_resynced=self._listening_resynced
        )
        self.participation = Participation(
            self.deadlines,
            LEASE_SECONDS,
            self._tell_changed,
            self._tell_window_changed,
            self.wall_clock,
            self._groups_of,
            self._listens_to_standing,
            self.figure_sources,
            Hearing(MemberHearingRecord(MemberHearing(now=self.deadlines.now)), enabled=self.hears),
            self.voices,
        )

    async def _groups_of(self, connection: HTTPConnection) -> frozenset[str] | None:
        async with StepTimer("group_read.ended", _STEPS) as step:
            groups = await self.groups.of_connection(connection)
            if isinstance(groups, AccessTokenRefusal):
                step.refuse(grant_refusal_reason(groups))
        return None if isinstance(groups, AccessTokenRefusal) else groups

    def _listens_to_standing(self, person: Person) -> bool:
        return _principal_of_person(person) in self._standing_listeners

    def _listening_changed(self, _principal: Principal, uri: str, _listens: bool) -> None:
        """Only a change on the standing reaches the offers. The callable never blocks: the read runs as a task of its
        own."""
        if uri == STANDING_URI:
            self._follow_listeners()

    def _listening_resynced(self) -> None:
        self._follow_listeners()

    def _follow_listeners(self) -> None:
        self._start(self._read_listeners(), OFFERS_RECOMPUTE_TASK_NAME, _log_offers_recompute_failed)

    async def _read_listeners(self) -> None:
        """Reads who listens and recomputes the offers on it. Only the newest read counts: an older one that ends later
        would put back a listener that has gone."""
        self._listeners_reads += 1
        read = self._listeners_reads
        try:
            listeners = await self.changes.listeners(STANDING_URI)
        except PlaceStoreError:
            logger.error(LISTENERS_READ_FAILED_EVENT)
            listeners = frozenset()
        if read == self._listeners_reads:
            self._standing_listeners = listeners
            await self.current().listening_changed()

    def _tell_changed(self, person: Person) -> None:
        self._start(
            self._announce(_principal_of_person(person), STANDING_URI, tools_too=True),
            PLACE_SIGNAL_TASK_NAME,
            _log_place_signal_failed,
        )

    def _tell_window_changed(self, person: Person, room_id: str) -> None:
        self._start(
            self._announce(_principal_of_person(person), _call_window_uri(room_id), tools_too=False),
            PLACE_SIGNAL_TASK_NAME,
            _log_place_signal_failed,
        )

    async def _announce(self, principal: Principal, uri: str, *, tools_too: bool) -> None:
        """The change stands whether or not the store carries it: a failed signal is logged once, and the agent sees
        the change at its next listen's read. The tools notice goes first, so that a runner that lists the tools again
        on it has the new list when the change wakes its turn; a refused tools notice does not hold the wake back."""
        try:
            if tools_too:
                try:
                    await self.changes.tools_changed(principal)
                except PlaceStoreError:
                    logger.error(PLACE_SIGNAL_FAILED_EVENT)
            await self.changes.changed(principal, uri)
        except PlaceStoreError:
            logger.error(PLACE_SIGNAL_FAILED_EVENT)

    def _start(
        self, work: Coroutine[None, None, None], name: str, log_failure: Callable[[BaseException], None]
    ) -> None:
        """The synchronous hooks start their async work here."""
        self._tasks.start(work, log_failure, name=name)

    async def shut_down(self) -> None:
        await self._tasks.wait()

    async def offered_tools(  # NOSONAR(S7503) OfferedTools is async
        self, declared: Collection[str], principal: Principal
    ) -> Collection[str]:
        """Every declared tool, and the gesture and framing tools only while the principal's agent shows a figure in
        some call."""
        shown = self.participation.figures.shown_by(Person(principal.iss, principal.sub))
        return tuple(name for name in declared if name not in FIGURE_TOOLS or shown)

    def current(self) -> Participation:
        return self.participation


@dataclass(frozen=True)
class BuiltParticipationFeature:
    """What participation adds to the app: its call feature, the MCP route and the resource a token reaches it by."""

    feature: TransitionFeature
    router: APIRouter
    bearer_resource: ProtectedResource


def _log_offers_recompute_failed(error: BaseException) -> None:
    logger.error(OFFERS_RECOMPUTE_FAILED_EVENT, exc_info=error)


def _log_place_signal_failed(error: BaseException) -> None:
    logger.error(PLACE_SIGNAL_FAILED_EVENT, exc_info=error)


def _call_window_uri(room_id: str) -> str:
    return place_window_uri(PlaceRef(kind=CALL_KIND, id=room_id))


def _principal_of_person(person: Person) -> Principal:
    return Principal(iss=person.issuer, sub=person.subject)


def _principal_of(issuer: str, scope: Scope) -> Principal:
    person = resource_person(HTTPConnection(scope))
    # webauth binds the store to one issuer, and an account's id is that issuer's `sub`; the issuer goes out exactly
    # as the relying party holds it.
    return Principal(iss=issuer, sub=person.id)


def _own_account_of(scope: Scope) -> OwnAccount | None:
    facts = resource_person(HTTPConnection(scope)).agent
    return None if facts is None else OwnAccount(facts.owner, facts.name)


def _caller_of(request: PersonRequest) -> Caller:
    return Caller(Person(request.principal.iss, request.principal.sub), _own_account_of(request.scope))


@dataclass(frozen=True)
class _LeaseRenewingFront:
    """The person front behind a lease: every admitted call of an agent with an account of its own renews that agent's
    lease and keeps its exchanged hearing token before the front serves it, the opening of a listen included."""

    front: ASGIApp
    issuer: str
    participation: Callable[[], Participation]
    hearing_tokens: HearingTokens

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        own_account = _own_account_of(scope)
        if own_account is not None:
            principal = _principal_of(self.issuer, scope)
            person = Person(principal.iss, principal.sub)
            await self.participation().agent_called(person, own_account)
            await self.hearing_tokens.keep(person, HTTPConnection(scope))
        await self.front(scope, receive, send)


def _as_person_tool(tool: CallerTool, opening: VideoOpening | None) -> PersonTool:
    """The tool as the front serves it; an accept that is accepted starts the agent's video session, when video is on,
    once the tool has answered."""

    async def handler(request: PersonRequest, arguments: JsonObject) -> ToolOutcome:
        caller = _caller_of(request)
        refusal = refusal_to_serve(tool.declaration.name, caller)
        if refusal is not None:
            return refused(refusal)
        outcome = await tool.handler(caller, arguments)
        if opening is not None and tool.declaration.name == ACCEPT_INVITATION_TOOL and not outcome.is_error:
            opening.after_accept(caller.person, video_looks_of_accept(arguments), HTTPConnection(request.scope))
        return outcome

    return PersonTool(tool.declaration, handler)


def _standing_of(standing: PlaceStanding) -> Standing:
    invited = standing.invitation
    invitation = (
        None
        if invited is None
        else Invitation(id=invited.invitation_id, epoch=invited.epoch, expires_at=invited.expires_at)
    )
    return Standing(
        place=PlaceRef(kind=CALL_KIND, id=standing.room_id),
        state=StandingState(standing.state.value),
        invitation=invitation,
    )


def _window_of(window: WindowRead) -> PlaceWindow:
    return PlaceWindow(
        generation=window.generation,
        starts_at=window.starts_at,
        entries=tuple(
            PlaceEntry(
                seq=line.seq,
                kind=line.kind.value,
                name=line.name,
                text=line.text,
                own=line.own,
                spoken=line.spoken,
                cut=line.cut,
                continues=line.continues,
                traceparent=line.traceparent,
            )
            for line in window.lines
        ),
        talked_over=None if window.talked_over is None else _talked_over_of(window.talked_over),
        interrupted=None if window.interrupted is None else Interrupted(id=window.interrupted),
        voice_state=None if window.voice is None else VoiceState(window.voice.value),
    )


def _talked_over_of(record: WindowTalkedOver) -> TalkedOver:
    return TalkedOver(
        id=record.id,
        playing=record.playing,
        speech=tuple(PartialSpeech(name=partial.name, text=partial.text) for partial in record.speech),
    )


@dataclass(frozen=True)
class _MemberResources:
    """What a principal reads: the standing of an agent with an account of its own, and the chat window of every call
    where the principal holds a joined agent."""

    participation: Callable[[], Participation]

    async def current(self, principal: Principal) -> Collection[str]:  # NOSONAR(S7503) MemberResources is async
        person = Person(principal.iss, principal.sub)
        windows = tuple(_call_window_uri(room_id) for room_id in self.participation().window_rooms_of(person))
        return (STANDING_URI, *windows) if self.participation().is_live_agent(person) else windows

    async def read(  # NOSONAR(S7503) MemberResources is async
        self, principal: Principal, uri: str
    ) -> MemberStanding | PlaceWindow | None:
        person = Person(principal.iss, principal.sub)
        if uri == STANDING_URI:
            if not self.participation().is_live_agent(person):
                return None
            return MemberStanding(standings=tuple(map(_standing_of, await self.participation().standings_of(person))))
        place = place_ref_from_window_uri(uri)
        if place is None or place.kind != CALL_KIND:
            return None
        window = self.participation().read_window(person, place.id)
        return None if window is None else _window_of(window)


def _file_endpoint(
    store: FigureStore, named_by: Callable[[str], tuple[NamedFigure, ...]]
) -> Callable[[str], Awaitable[Response]]:
    """The route of a figure or of a clip, by the store and the names of its kind: any signed-in member reads the
    verified file of a digest a joined agent names, since the file is a public release asset."""

    async def figure(sha256: str) -> Response:
        named = named_by(sha256)
        if not named:
            raise HTTPException(status_code=404)
        body = await store.bytes_of(sha256, named)
        if body is None:
            raise HTTPException(status_code=502)
        return Response(
            body,
            media_type=GLB_MEDIA_TYPE,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": IMMUTABLE_ASSET_CACHE_CONTROL},
        )

    return figure


def _add_file_routes(
    router: APIRouter,
    stores: Mapping[ServedFileKind, FigureStore],
    participation: Callable[[], Participation],
) -> None:
    """One route per served file kind, each answering only a file the current participation names."""
    for kind, store in stores.items():
        router.add_api_route(
            f"{kind.path}/{{sha256}}",
            _file_endpoint(store, partial(_named_by_participation, kind, participation)),
            methods=READ_METHODS,
        )


def _named_by_participation(
    kind: ServedFileKind, participation: Callable[[], Participation], sha256: str
) -> tuple[NamedFigure, ...]:
    return participation().figures.named(kind, sha256)


@dataclass(frozen=True)
class ParticipationFeature:
    """Participation for the app: agents dial in through HopIn's MCP endpoint at the app's public origin."""

    public_origin: str
    # The Redis that carries an agent's place signals between HopIn's processes.
    redis: AsyncRedis = field(kw_only=True)
    # The realm's names for the MCP path.
    realm: RealmSettings = field(kw_only=True)
    figure_sources: tuple[FigureSource, ...] = ()
    figure_transport: FigureTransport = get_figure_over_https
    hearing: SpeechService | None = None
    speaking: SpeechService | None = None
    video: VideoService | None = None
    voices: VoiceStore = field(default_factory=VoiceStore)
    subscription_wait_seconds: float = PLACE_SUBSCRIPTION_WAIT_SECONDS

    @property
    def sign_in_scopes(self) -> tuple[str, ...]:
        """None: an agent signs in at its own client, not through the page."""
        return ()

    @property
    def frame_sources(self) -> tuple[str, ...]:
        """None: participation frames nothing."""
        return ()

    def __call__(
        self,
        *,
        relying_party: OidcRelyingParty,
        session_transaction: SessionTransaction,
        auth_config: WebAuthConfig,
        scheduler: Scheduler,
        deadlines: Deadlines,
        request_language: RequestLanguage,
        page_build: PageBuild,
        turn_relay: TurnRelay | None,
    ) -> BuiltParticipationFeature:
        del request_language, page_build
        built = _BuiltParticipation(
            deadlines,
            auth_config.wall_clock,
            SessionGroups(relying_party, session_transaction, auth_config),
            self.figure_sources,
            hears=self.hearing is not None,
            voices=self.voices,
            places=RedisPlaceStore(self.redis, key_prefix=REDIS_AGENTS_KEY_PREFIX),
        )
        hearing_tokens = self._hearing_tokens(deadlines, scheduler, auth_config)
        voice = self._voice(relying_party, hearing_tokens)
        stores = {kind: self._store_of(kind) for kind in SERVED_FILE_KINDS}
        opening = self._opening(relying_party, turn_relay, built, scheduler, auth_config)
        front = self._front(built, relying_party, voice, opening)

        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            try:
                async with AsyncExitStack() as stack:
                    await self._enter_bounded(stack, front)
                    yield
            finally:
                if opening is not None:
                    await opening.shut_down()
                await built.shut_down()
                for store in stores.values():
                    store.shut_down()

        router = APIRouter(lifespan=lifespan)
        # No methods named, so every method reaches the front, which refuses all but POST itself.
        router.routes.append(
            Route(
                MCP_ENDPOINT_PATH,
                endpoint=_LeaseRenewingFront(front, relying_party.issuer, built.current, hearing_tokens),
            )
        )
        _add_file_routes(router, stores, built.current)
        self._add_voice_route(router, built.current)
        self._add_hearing_route(router, built.current, relying_party, scheduler, hearing_tokens)
        return BuiltParticipationFeature(built.participation, router, self._resource())

    def _store_of(self, kind: ServedFileKind) -> FigureStore:
        """The store of one kind of file, with its byte bound, cache and step line."""
        return FigureStore(
            partial(fetch_figure, self.figure_transport, monotonic=time.monotonic, max_bytes=kind.max_bytes),
            cache_bytes=kind.cache_bytes,
            step=kind.fetch_step,
        )

    def _add_hearing_route(
        self,
        router: APIRouter,
        participation: Callable[[], Participation],
        relying_party: OidcRelyingParty,
        scheduler: Scheduler,
        hearing_tokens: HearingTokens,
    ) -> None:
        """Serve the hearing socket when a hearing service is wired."""
        if self.hearing is None:
            return
        hearing_route = HearingRoute(
            service=self.hearing,
            issuer=relying_party.issuer,
            resolver=SessionIdentityResolver(),
            participation=participation,
            tokens=hearing_tokens,
            relying_party=relying_party,
            scheduler=scheduler,
        )
        router.add_api_websocket_route(f"{HEARING_PATH}/{{seat}}", hearing_route.serve)

    async def _enter_bounded(self, stack: AsyncExitStack, front: McpPersonFront) -> None:
        """Enter the front, failing the start loudly when its first subscription is not confirmed in time."""
        try:
            async with asyncio.timeout(self.subscription_wait_seconds):
                await stack.enter_async_context(front.running())
        except TimeoutError:
            wait_ms = timedelta(seconds=self.subscription_wait_seconds) / timedelta(milliseconds=1)
            logger.error(PLACE_SUBSCRIPTION_TIMED_OUT_EVENT, wait_ms=wait_ms)
            raise

    def _opening(
        self,
        relying_party: OidcRelyingParty,
        turn_relay: TurnRelay | None,
        built: _BuiltParticipation,
        scheduler: Scheduler,
        auth_config: WebAuthConfig,
    ) -> VideoOpening | None:
        """The open of an agent's video session: only with the video service, TURN and agents' speaking all on."""
        if self.video is None or turn_relay is None or self.speaking is None:
            return None
        return VideoOpening(
            self.video,
            relying_party=relying_party,
            turn_relay=turn_relay,
            participation=built.current,
            scheduler=scheduler,
            wall_clock=auth_config.wall_clock,
        )

    def _front(
        self,
        built: _BuiltParticipation,
        relying_party: OidcRelyingParty,
        voice: AgentVoice | None,
        opening: VideoOpening | None,
    ) -> McpPersonFront:
        tools = [_as_person_tool(tool, opening) for tool in participation_tools(built.current, voice)]
        return McpPersonFront(
            tools,
            resolve_person=partial(_principal_of, relying_party.issuer),
            resources=_MemberResources(built.current),
            changes=built.changes,
            offered_tools=partial(built.offered_tools, [tool.declaration.name for tool in tools]),
        )

    def _add_voice_route(self, router: APIRouter, participation: Callable[[], Participation]) -> None:
        """Serve the stored sounds to the signed-in accounts that hold a seat in the sound's room."""
        router.add_api_route(
            f"{VOICE_PATH}/{{voice_id}}",
            voice_endpoint(self.voices, lambda account, room: participation().holds_seat(account, room)),
            methods=READ_METHODS,
        )

    def _resource(self) -> ProtectedResource:
        return ProtectedResource(
            url=f"{self.public_origin}{MCP_ENDPOINT_PATH}",
            scope=self.realm.mcp_scope,
            required_group=self.realm.mcp_group,
            agent_group=self.realm.agent_group,
            authorized_parties=AuthorizedParties(frozenset({self.realm.mcp_client_id})),
            admits_client_audience=True,
        )

    def _voice(self, relying_party: OidcRelyingParty, tokens: HearingTokens) -> AgentVoice | None:
        if self.speaking is None:
            return None
        renderer = SpeechRenderer(
            self.speaking,
            SPEAK_DEADLINE_SECONDS,
            SPEAK_MAX_BYTES,
            relying_party,
            SPEECH_STREAM,
        )
        return AgentVoice(renderer, tokens)

    def _hearing_tokens(self, deadlines: Deadlines, scheduler: Scheduler, auth_config: WebAuthConfig) -> HearingTokens:
        """HopIn holds an agent's token for the scopes of each service the agent hears or speaks through."""
        services = [service for service in (self.hearing, self.speaking) if service is not None]
        scopes = tuple(dict.fromkeys(service.audience_scope for service in services))
        return HearingTokens(
            scopes,
            lease_seconds=LEASE_SECONDS,
            deadlines=deadlines,
            scheduler=scheduler,
            wall_clock=auth_config.wall_clock,
        )
