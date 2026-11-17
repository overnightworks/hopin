"""HopIn's MCP surface: the person front a person's own agent calls with a bearer token, and participation built to
serve it."""

import asyncio
import logging
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog
from agents.mcp_server import McpPersonFront, PersonRequest, PersonTool, PlaceStore, PlaceStoreError, ResourceChanges
from agents.mcp_server.hearing import MemberHearing
from agents.mcp_server.redis_place_store import RedisPlaceStore
from agents.mcp_wire import MCP_ENDPOINT_PATH
from agents.member import (
    GESTURE_TOOL,
    INVITATION_SECONDS,
    LEASE_SECONDS,
    STANDING_URI,
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
from hopin.agent.tools import CALL_KIND, CallerTool, participation_tools, refusal_to_serve, refused
from hopin.config.constants import IMMUTABLE_ASSET_CACHE_CONTROL, READ_METHODS, REDIS_AGENTS_KEY_PREFIX
from hopin.participation.feature import Participation
from hopin.participation.figure_transport import get_figure_over_https
from hopin.participation.figures import FigureSource, FigureStore, FigureTransport, NamedFigure, fetch_figure
from hopin.participation.hearing import Hearing
from hopin.participation.participants import Caller, OwnAccount, Person
from hopin.participation.vocabulary import (
    CLIP_CACHE_BYTES,
    CLIP_MAX_BYTES,
    CLIP_PATH,
    FIGURE_PATH,
    HEARING_PATH,
    VOICE_PATH,
)
from hopin.participation.voice import VoiceStore, voice_endpoint
from hopin.signaling.identity import SessionIdentityResolver
from hopin.stores.session_transaction import session_transaction_of

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Coroutine, Mapping

    from fastapi import FastAPI
    from redis.asyncio import Redis as AsyncRedis
    from starlette.types import ASGIApp, Receive, Scope, Send
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore, SignInTransaction

    from hopin.agent.speech_settings import SpeechService
    from hopin.participation.chat_window import WindowRead, WindowTalkedOver
    from hopin.participation.places import PlaceStanding
    from hopin.signaling.features import CallFeature, CallRooms, FeatureFactory
    from hopin.signaling.scheduler import Deadlines, Scheduler
    from hopin.web.request_language import RequestLanguage

logger: structlog.stdlib.BoundLogger = structlog.get_logger()
_STEPS: Final = logging.getLogger(__name__)

# The realm's objects for HopIn's MCP path: the scope and audience a token carries, the group whose members the
# gate admits, the group of agents' own accounts it admits from any client, and the public client an agent signs in
# with.
MCP_SCOPE: Final = "mcp-hopin"
MCP_GROUP: Final = "/apps/hopin/mcp"
AGENT_GROUP: Final = "/agents"
MCP_CLIENT_ID: Final = "agents"
GLB_MEDIA_TYPE: Final = "model/gltf-binary"
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
    """The participation the call's handler builds, which the tools reach once it exists. Its resource changes tell
    the participation when an agent starts or stops listening on its standing; the place store carries them between
    HopIn's processes."""

    deadlines: Deadlines
    wall_clock: Callable[[], float]
    adders: Mapping[str, str]
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
    _tasks: set[asyncio.Task[None]] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self.changes = ResourceChanges(
            self.places, listening_changed=self._listening_changed, listening_resynced=self._listening_resynced
        )

    def build(self, rooms: CallRooms) -> CallFeature:
        self.participation = Participation(
            rooms,
            self.deadlines,
            LEASE_SECONDS,
            INVITATION_SECONDS,
            self._tell_changed,
            self._tell_window_changed,
            self.wall_clock,
            self.adders,
            self._groups_of,
            self._listens_to_standing,
            self.figure_sources,
            Hearing(MemberHearingRecord(MemberHearing(now=self.deadlines.now)), enabled=self.hears),
            self.voices,
        )
        return self.participation

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
        self._start(self._read_listeners(), OFFERS_RECOMPUTE_TASK_NAME, OFFERS_RECOMPUTE_FAILED_EVENT)

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
            PLACE_SIGNAL_FAILED_EVENT,
        )

    def _tell_window_changed(self, person: Person, room_id: str) -> None:
        self._start(
            self._announce(_principal_of_person(person), _call_window_uri(room_id), tools_too=False),
            PLACE_SIGNAL_TASK_NAME,
            PLACE_SIGNAL_FAILED_EVENT,
        )

    async def _announce(self, principal: Principal, uri: str, *, tools_too: bool) -> None:
        """The change stands whether or not the store carries it: a failed signal is logged once, and the agent sees
        the change at its next listen's read."""
        try:
            await self.changes.changed(principal, uri)
            if tools_too:
                await self.changes.tools_changed(principal)
        except PlaceStoreError:
            logger.error(PLACE_SIGNAL_FAILED_EVENT)

    def _start(self, work: Coroutine[None, None, None], name: str, failed_event: str) -> None:
        """The synchronous hooks start their async work here; the set keeps each task until it ends."""
        task = asyncio.create_task(work, name=name)
        self._tasks.add(task)
        task.add_done_callback(partial(self._ended, failed_event))

    def _ended(self, failed_event: str, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error(failed_event, exc_info=error)

    async def shut_down(self) -> None:
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def offered_tools(  # NOSONAR(S7503) OfferedTools is async
        self, declared: Collection[str], principal: Principal
    ) -> Collection[str]:
        """Every declared tool, and the gesture tool only while the principal's agent shows a figure in some call."""
        shown = self.participation.figures.shown_by(Person(principal.iss, principal.sub))
        return tuple(name for name in declared if name != GESTURE_TOOL or shown)

    def current(self) -> Participation:
        return self.participation


@dataclass(frozen=True)
class BuiltParticipationFeature:
    """What participation adds to the app: its call feature, the MCP route and the resource a token reaches it by."""

    factory: FeatureFactory
    router: APIRouter
    bearer_resource: ProtectedResource


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


def _as_person_tool(tool: CallerTool) -> PersonTool:
    async def handler(request: PersonRequest, arguments: JsonObject) -> ToolOutcome:
        caller = _caller_of(request)
        refusal = refusal_to_serve(tool.declaration.name, caller)
        return refused(refusal) if refusal is not None else await tool.handler(caller, arguments)

    return PersonTool(tool.declaration, handler)


def _standing_of(standing: PlaceStanding) -> Standing:
    invited = standing.invitation
    invitation = (
        None
        if invited is None
        else Invitation(
            id=invited.invitation_id, epoch=invited.epoch, expires_at=datetime.fromtimestamp(invited.expires_at, UTC)
        )
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
                traceparent=None,
            )
            for line in window.lines
        ),
        talked_over=None if window.talked_over is None else _talked_over_of(window.talked_over),
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
            return MemberStanding(standings=tuple(map(_standing_of, self.participation().standings_of(person))))
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
    router: APIRouter, store: FigureStore, clip_store: FigureStore, participation: Callable[[], Participation]
) -> None:
    """The figure and clip routes, each answering only a file the current participation names."""
    router.add_api_route(
        f"{FIGURE_PATH}/{{sha256}}",
        _file_endpoint(store, lambda sha256: participation().figures.named(sha256)),
        methods=READ_METHODS,
    )
    router.add_api_route(
        f"{CLIP_PATH}/{{sha256}}",
        _file_endpoint(clip_store, lambda sha256: participation().figures.named_clips(sha256)),
        methods=READ_METHODS,
    )


@dataclass(frozen=True)
class ParticipationFeature:
    """Participation for the app: agents dial in through HopIn's MCP endpoint at the app's public origin."""

    public_origin: str
    # The Redis that carries an agent's place signals between HopIn's processes.
    redis: AsyncRedis = field(kw_only=True)
    agent_adders: Mapping[str, str] = field(default_factory=dict)
    figure_sources: tuple[FigureSource, ...] = ()
    figure_transport: FigureTransport = get_figure_over_https
    hearing: SpeechService | None = None
    speaking: SpeechService | None = None
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
        sessions: SessionRecordStore,
        auth_config: WebAuthConfig,
        scheduler: Scheduler,
        deadlines: Deadlines,
        transaction: SignInTransaction,
        request_language: RequestLanguage,
    ) -> BuiltParticipationFeature:
        del request_language
        built = _BuiltParticipation(
            deadlines,
            auth_config.wall_clock,
            self.agent_adders,
            SessionGroups(relying_party, session_transaction_of(sessions), auth_config),
            self.figure_sources,
            hears=self.hearing is not None,
            voices=self.voices,
            places=RedisPlaceStore(self.redis, key_prefix=REDIS_AGENTS_KEY_PREFIX),
        )
        hearing_tokens = self._hearing_tokens(deadlines, auth_config)
        voice = self._voice(relying_party, hearing_tokens)
        store = FigureStore(partial(fetch_figure, self.figure_transport, monotonic=time.monotonic))
        clip_store = self._clip_store()
        front = self._front(built, relying_party, voice)

        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            try:
                async with AsyncExitStack() as stack:
                    await self._enter_bounded(stack, front)
                    yield
            finally:
                await built.shut_down()
                store.shut_down()
                clip_store.shut_down()

        router = APIRouter(lifespan=lifespan)
        # No methods named, so every method reaches the front, which refuses all but POST itself.
        router.routes.append(
            Route(
                MCP_ENDPOINT_PATH,
                endpoint=_LeaseRenewingFront(front, relying_party.issuer, built.current, hearing_tokens),
            )
        )
        _add_file_routes(router, store, clip_store, built.current)
        self._add_voice_route(router, built.current)
        self._add_hearing_route(router, built.current, relying_party, sessions, scheduler, hearing_tokens)
        return BuiltParticipationFeature(built.build, router, self._resource(transaction))

    def _clip_store(self) -> FigureStore:
        """The store of the clips, with its own byte bound, cache and step line apart from the figure's."""
        return FigureStore(
            partial(
                fetch_figure,
                self.figure_transport,
                monotonic=time.monotonic,
                max_bytes=CLIP_MAX_BYTES,
                redirect_sources=self.figure_sources,
            ),
            cache_bytes=CLIP_CACHE_BYTES,
            step_event="clip_fetch.ended",
        )

    def _add_hearing_route(
        self,
        router: APIRouter,
        participation: Callable[[], Participation],
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        scheduler: Scheduler,
        hearing_tokens: HearingTokens,
    ) -> None:
        """Serve the hearing socket when a hearing service is wired."""
        if self.hearing is None:
            return
        hearing_route = HearingRoute(
            service=self.hearing,
            issuer=relying_party.issuer,
            resolver=SessionIdentityResolver(sessions),
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
            logger.error(PLACE_SUBSCRIPTION_TIMED_OUT_EVENT, wait_seconds=self.subscription_wait_seconds)
            raise

    def _front(
        self, built: _BuiltParticipation, relying_party: OidcRelyingParty, voice: AgentVoice | None
    ) -> McpPersonFront:
        tools = [_as_person_tool(tool) for tool in participation_tools(built.current, voice)]
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

    def _resource(self, transaction: SignInTransaction) -> ProtectedResource:
        return ProtectedResource(
            url=f"{self.public_origin}{MCP_ENDPOINT_PATH}",
            scope=MCP_SCOPE,
            required_group=MCP_GROUP,
            agent_group=AGENT_GROUP,
            authorized_parties=AuthorizedParties(frozenset({MCP_CLIENT_ID})),
            admits_client_audience=True,
            transaction=transaction,
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

    def _hearing_tokens(self, deadlines: Deadlines, auth_config: WebAuthConfig) -> HearingTokens:
        """HopIn holds an agent's token for the scopes of each service the agent hears or speaks through."""
        services = [service for service in (self.hearing, self.speaking) if service is not None]
        scopes = tuple(dict.fromkeys(service.audience_scope for service in services))
        return HearingTokens(
            scopes, lease_seconds=LEASE_SECONDS, deadlines=deadlines, wall_clock=auth_config.wall_clock
        )
