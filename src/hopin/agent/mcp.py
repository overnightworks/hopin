"""HopIn's MCP surface: the person front a person's own agent calls with a bearer token, and participation built to
serve it."""

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final

from agent_runtime_client.mcp_server import McpPersonFront, PersonRequest, PersonTool
from agent_runtime_client.mcp_wire import MCP_ENDPOINT_PATH
from agent_runtime_client.tools import HandTool, JsonObject, Principal, ToolOutcome
from fastapi import APIRouter
from starlette.requests import HTTPConnection
from starlette.routing import Route
from webauth.middleware import ProtectedResource, resource_person
from webauth.policies import AuthorizedParties

from hopin.agent.tools import participation_tools
from hopin.participation.feature import Participation

if TYPE_CHECKING:
    from starlette.types import Scope
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore, SignInTransaction

    from hopin.auth.refresh_grants import SessionGrants
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

    participation: Participation = field(init=False)

    def build(self, rooms: CallRooms) -> CallFeature:
        self.participation = Participation(rooms)
        return self.participation

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


def _as_person_tool(tool: HandTool) -> PersonTool:
    async def handler(request: PersonRequest, arguments: JsonObject) -> ToolOutcome:
        return await tool.handler(request.principal, arguments)

    return PersonTool(tool.declaration, handler)


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
        grants: SessionGrants,
    ) -> BuiltParticipationFeature:
        del sessions, auth_config, scheduler, grants
        resource = ProtectedResource(
            url=f"{self.public_origin}{MCP_ENDPOINT_PATH}",
            scope=MCP_SCOPE,
            required_group=MCP_GROUP,
            agent_group=AGENT_GROUP,
            authorized_parties=AuthorizedParties(frozenset({MCP_CLIENT_ID})),
            transaction=transaction,
        )
        built = _BuiltParticipation()
        front = McpPersonFront(
            [_as_person_tool(tool) for tool in participation_tools(built.current)],
            resolve_person=partial(_principal_of, relying_party.issuer),
        )
        router = APIRouter()
        # No methods named, so every method reaches the front, which refuses all but POST itself.
        router.routes.append(Route(MCP_ENDPOINT_PATH, endpoint=front))
        return BuiltParticipationFeature(built.build, router, resource)
