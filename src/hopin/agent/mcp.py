"""HopIn's MCP surface: the person front a person's own agent calls with a bearer token, and participation built to
serve it."""

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final

from agent_runtime_client.mcp_server import McpPersonFront, PersonRequest, PersonTool
from agent_runtime_client.mcp_server.wait import InMemoryMemberChanges, wait_tool
from agent_runtime_client.mcp_wire import MCP_ENDPOINT_PATH
from agent_runtime_client.tools import JsonObject, Principal, ToolOutcome
from agent_runtime_client.wait import LEASE_SECONDS, PlaceRef, Standing, StandingState
from fastapi import APIRouter
from starlette.requests import HTTPConnection
from starlette.routing import Route
from webauth.middleware import ProtectedResource, resource_person
from webauth.policies import AuthorizedParties

from hopin.agent.tools import CallerTool, participation_tools, refused
from hopin.participation.feature import Caller, OwnAccount, Participation, Person
from hopin.participation.vocabulary import AgentRefusal

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.types import ASGIApp, Receive, Scope, Send
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore, SignInTransaction

    from hopin.signaling.features import CallFeature, CallRooms, FeatureFactory
    from hopin.signaling.scheduler import Scheduler

# The realm's objects for HopIn's MCP path: the scope and audience a token carries, the group whose members the
# gate admits, the group of agents' own accounts it admits from any client, and the public client an agent signs in
# with.
MCP_SCOPE: Final = "mcp-hopin"
MCP_GROUP: Final = "/apps/hopin/mcp"
AGENT_GROUP: Final = "/agents"
MCP_CLIENT_ID: Final = "agents"


@dataclass
class _BuiltParticipation:
    """The participation the call's handler builds, which the tools reach once it exists."""

    scheduler: Scheduler
    changes: InMemoryMemberChanges
    participation: Participation = field(init=False)

    def build(self, rooms: CallRooms) -> CallFeature:
        self.participation = Participation(rooms, self.scheduler, LEASE_SECONDS, self._tell_changed)
        return self.participation

    def _tell_changed(self, person: Person) -> None:
        self.changes.changed(Principal(iss=person.issuer, sub=person.subject))

    def current(self) -> Participation:
        return self.participation


@dataclass(frozen=True)
class BuiltParticipationFeature:
    """What participation adds to the app: its call feature, the MCP route and the resource a token reaches it by."""

    factory: FeatureFactory
    router: APIRouter
    bearer_resource: ProtectedResource


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
    lease before the front serves it, `wait` included."""

    front: ASGIApp
    issuer: str
    participation: Callable[[], Participation]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        own_account = _own_account_of(scope)
        if own_account is not None:
            principal = _principal_of(self.issuer, scope)
            self.participation().agent_called(Person(principal.iss, principal.sub), own_account)
        await self.front(scope, receive, send)


def _as_person_tool(tool: CallerTool) -> PersonTool:
    async def handler(request: PersonRequest, arguments: JsonObject) -> ToolOutcome:
        return await tool.handler(_caller_of(request), arguments)

    return PersonTool(tool.declaration, handler)


def _wait_for_own_accounts(participation: Callable[[], Participation], changes: InMemoryMemberChanges) -> PersonTool:
    """The runtime's wait tool, served only to an agent with an account of its own."""

    async def standings(principal: Principal) -> tuple[Standing, ...]:  # NOSONAR(S7503) CurrentStandings is async
        rooms = participation().standings_of(Person(principal.iss, principal.sub))
        return tuple(Standing(place=PlaceRef(kind="call", id=room), state=StandingState.ACTIVE) for room in rooms)

    waiting = wait_tool(changes, standings)

    async def handler(request: PersonRequest, arguments: JsonObject) -> ToolOutcome:
        if _caller_of(request).own_account is None:
            return refused(AgentRefusal.OWN_ACCOUNT_ONLY)
        return await waiting.handler(request, arguments)

    return PersonTool(waiting.declaration, handler)


@dataclass(frozen=True)
class ParticipationFeature:
    """Participation for the app: agents dial in through HopIn's MCP endpoint at the app's public origin."""

    public_origin: str

    @property
    def sign_in_scopes(self) -> tuple[str, ...]:
        """None: an agent signs in at its own client, not through the page."""
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
        del sessions, auth_config
        resource = ProtectedResource(
            url=f"{self.public_origin}{MCP_ENDPOINT_PATH}",
            scope=MCP_SCOPE,
            required_group=MCP_GROUP,
            agent_group=AGENT_GROUP,
            authorized_parties=AuthorizedParties(frozenset({MCP_CLIENT_ID})),
            transaction=transaction,
        )
        built = _BuiltParticipation(scheduler, InMemoryMemberChanges())
        front = McpPersonFront(
            [
                *(_as_person_tool(tool) for tool in participation_tools(built.current)),
                _wait_for_own_accounts(built.current, built.changes),
            ],
            resolve_person=partial(_principal_of, relying_party.issuer),
        )
        router = APIRouter()
        # No methods named, so every method reaches the front, which refuses all but POST itself.
        router.routes.append(
            Route(MCP_ENDPOINT_PATH, endpoint=_LeaseRenewingFront(front, relying_party.issuer, built.current))
        )
        return BuiltParticipationFeature(built.build, router, resource)
