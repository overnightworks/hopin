"""HopIn's MCP surface: the person front a person's own agent calls with a bearer token, and participation built to
serve it."""

import time
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Final

import structlog
from agents.mcp_server import McpPersonFront, PersonRequest, PersonTool, ResourceChanges
from agents.mcp_wire import MCP_ENDPOINT_PATH
from agents.member import (
    INVITATION_SECONDS,
    LEASE_SECONDS,
    STANDING_URI,
    Invitation,
    MemberStanding,
    PlaceEntry,
    PlaceRef,
    PlaceWindow,
    Standing,
    StandingState,
    place_ref_from_window_uri,
    place_window_uri,
)
from agents.tools import JsonObject, Principal, ToolOutcome
from fastapi import APIRouter, HTTPException, Response
from starlette.requests import HTTPConnection
from starlette.routing import Route
from webauth.middleware import ProtectedResource, resource_person
from webauth.oidc_login import AccessTokenRefusal
from webauth.policies import AuthorizedParties
from webauth.session_groups import SessionGroups

from hopin.agent.tools import CALL_KIND, CallerTool, participation_tools, refusal_to_serve, refused
from hopin.config.constants import IMMUTABLE_ASSET_CACHE_CONTROL, READ_METHODS
from hopin.participation.feature import Caller, OwnAccount, Participation, Person
from hopin.participation.figure_transport import get_figure_over_https
from hopin.participation.figures import FigureSource, FigureStore, FigureTransport, fetch_figure
from hopin.participation.vocabulary import FIGURE_PATH

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping

    from fastapi import FastAPI
    from starlette.types import ASGIApp, Receive, Scope, Send
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore, SignInTransaction

    from hopin.participation.chat_window import WindowRead
    from hopin.participation.places import PlaceStanding
    from hopin.signaling.features import CallFeature, CallRooms, FeatureFactory
    from hopin.signaling.scheduler import Scheduler

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

# The realm's objects for HopIn's MCP path: the scope and audience a token carries, the group whose members the
# gate admits, the group of agents' own accounts it admits from any client, and the public client an agent signs in
# with.
MCP_SCOPE: Final = "mcp-hopin"
MCP_GROUP: Final = "/apps/hopin/mcp"
AGENT_GROUP: Final = "/agents"
MCP_CLIENT_ID: Final = "agents"
GLB_MEDIA_TYPE: Final = "model/gltf-binary"


@dataclass
class _BuiltParticipation:
    """The participation the call's handler builds, which the tools reach once it exists."""

    scheduler: Scheduler
    changes: ResourceChanges
    wall_clock: Callable[[], float]
    adders: Mapping[str, str]
    groups: SessionGroups
    figure_sources: tuple[FigureSource, ...]
    participation: Participation = field(init=False)

    def build(self, rooms: CallRooms) -> CallFeature:
        self.participation = Participation(
            rooms,
            self.scheduler,
            LEASE_SECONDS,
            INVITATION_SECONDS,
            self._tell_changed,
            self._tell_window_changed,
            self.wall_clock,
            self.adders,
            self._groups_of,
            self.figure_sources,
        )
        return self.participation

    async def _groups_of(self, connection: HTTPConnection) -> frozenset[str] | None:
        groups = await self.groups.of_connection(connection)
        if isinstance(groups, AccessTokenRefusal):
            logger.info("agent_adders_group_read_refused", reason=groups.reason.value)
            return None
        return groups

    def _tell_changed(self, person: Person) -> None:
        self.changes.changed(Principal(iss=person.issuer, sub=person.subject), STANDING_URI)

    def _tell_window_changed(self, person: Person, room_id: str) -> None:
        self.changes.changed(Principal(iss=person.issuer, sub=person.subject), _call_window_uri(room_id))

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
    lease before the front serves it, the opening of a listen included."""

    front: ASGIApp
    issuer: str
    participation: Callable[[], Participation]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        own_account = _own_account_of(scope)
        if own_account is not None:
            principal = _principal_of(self.issuer, scope)
            await self.participation().agent_called(Person(principal.iss, principal.sub), own_account)
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
            PlaceEntry(seq=line.seq, kind=line.kind.value, name=line.name, text=line.text, own=line.own)
            for line in window.lines
        ),
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


def _figure_endpoint(
    store: FigureStore, participation: Callable[[], Participation]
) -> Callable[[str], Awaitable[Response]]:
    """The route of a figure: any signed-in member reads the verified file of a digest a joined agent names, since the
    file is a public release asset."""

    async def figure(sha256: str) -> Response:
        named = participation().figures_named(sha256)
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


@dataclass(frozen=True)
class ParticipationFeature:
    """Participation for the app: agents dial in through HopIn's MCP endpoint at the app's public origin."""

    public_origin: str
    agent_adders: Mapping[str, str] = field(default_factory=dict)
    figure_sources: tuple[FigureSource, ...] = ()
    figure_transport: FigureTransport = get_figure_over_https

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
        transaction: SignInTransaction,
    ) -> BuiltParticipationFeature:
        resource = ProtectedResource(
            url=f"{self.public_origin}{MCP_ENDPOINT_PATH}",
            scope=MCP_SCOPE,
            required_group=MCP_GROUP,
            agent_group=AGENT_GROUP,
            authorized_parties=AuthorizedParties(frozenset({MCP_CLIENT_ID})),
            transaction=transaction,
        )
        built = _BuiltParticipation(
            scheduler,
            ResourceChanges(),
            auth_config.wall_clock,
            self.agent_adders,
            SessionGroups(relying_party, lambda: nullcontext(sessions), auth_config),
            self.figure_sources,
        )
        store = FigureStore(partial(fetch_figure, self.figure_transport, monotonic=time.monotonic))
        front = McpPersonFront(
            [_as_person_tool(tool) for tool in participation_tools(built.current)],
            resolve_person=partial(_principal_of, relying_party.issuer),
            resources=_MemberResources(built.current),
            changes=built.changes,
        )

        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            try:
                async with front.running():
                    yield
            finally:
                store.shut_down()

        router = APIRouter(lifespan=lifespan)
        # No methods named, so every method reaches the front, which refuses all but POST itself.
        router.routes.append(
            Route(MCP_ENDPOINT_PATH, endpoint=_LeaseRenewingFront(front, relying_party.issuer, built.current))
        )
        router.add_api_route(f"{FIGURE_PATH}/{{sha256}}", _figure_endpoint(store, built.current), methods=READ_METHODS)
        return BuiltParticipationFeature(built.build, router, resource)
