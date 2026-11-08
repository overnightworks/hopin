"""HopIn's real composition around participation: sockets on `/ws`, the MCP endpoint, and a fake issuer that signs
the agents' tokens."""

import json
import secrets
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_runtime_client.mcp_wire import LATEST_PROTOCOL_VERSION, PROTOCOL_VERSION_HEADER
from fastapi.testclient import TestClient
from webauth.admission import AppRole
from webauth.config import installed_web_auth_config
from webauth.ports import AccountKind

from hopin.agent.mcp import AGENT_GROUP, MCP_CLIENT_ID, MCP_GROUP, MCP_SCOPE, ParticipationFeature
from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import ADMIN_GROUP, LOGIN_GROUP, WEBSOCKET_PATH, SignalType
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signaling_fakes import ManualScheduler
from tests.support.signed_in import handshake_headers, seed_member

if TYPE_CHECKING:
    from collections.abc import Iterator

    from hopin.auth.stores import StoredAccount
    from tests.support.fake_issuer import FakeIssuer
    from tests.support.sign_in_settings import SignInSettings

METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"


class Seat:
    """One browser's socket: it numbers each frame after the join, as the page does."""

    def __init__(self, socket):
        self._socket = socket
        self._sent = 0
        self.participant_id = None
        self.room_id = None

    def join(self, room_id=None):
        join = {"type": SignalType.JOIN.value}
        if room_id is not None:
            join["room_id"] = room_id
        self._socket.send_json(join)
        joined = self._socket.receive_json()
        self.participant_id = joined["participant_id"]
        self.room_id = joined.get("room_id", room_id)
        return joined

    def settle(self):
        """Wait until the server has finished handling the join: it handles a seat's frames one after another, so
        the answer to a later frame comes after everything the join does. Only the host of a call may lock it."""
        self._sent += 1
        self._socket.send_json({"type": SignalType.LOCK_ROOM.value, "seq": self._sent})
        assert self.next_frame()["type"] == SignalType.ROOM_LOCKED.value

    def next_frame(self):
        frame = self._socket.receive_json()
        return {key: value for key, value in frame.items() if key != "seq"}


@dataclass
class Dialed:
    """What a tool call answered: the HTTP status and, for a call that ran, the tool's own answer."""

    status: int
    outcome: dict | None
    is_error: bool | None
    response: object


@dataclass
class DialedCall:
    """A HopIn instance whose issuer is a fake, with the means to seat people and to call its MCP endpoint."""

    issuer: FakeIssuer
    stores: SignInStores
    client: TestClient
    sockets: ExitStack
    audience: str
    scheduler: ManualScheduler

    def member(self, *, role=AppRole.USER):
        config = installed_web_auth_config(self.client.app)
        name = f"member-{secrets.token_hex(4)}"
        account = self.stores.accounts.create_for_subject(f"subject-{name}", name, role=role, kind=AccountKind.PERSON)
        return account, seed_member(self.stores, config, account=account)

    def seated(self):
        """A person in a call of their own: their account for a token, their login and their seat."""
        account, member = self.member()
        return account, member, self.seat(member)

    def seat(self, seeded, room_id=None):
        socket = self.sockets.enter_context(
            self.client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(seeded))
        )
        seat = Seat(socket)
        seat.join(room_id)
        if room_id is None:
            seat.settle()
        return seat

    def token(
        self,
        account: StoredAccount,
        *,
        audience=None,
        authorized_party=MCP_CLIENT_ID,
        scope=MCP_SCOPE,
        groups=(LOGIN_GROUP, MCP_GROUP),
        age_seconds=0,
    ):
        return self.issuer.access_token(
            account.id,
            audience=self.audience if audience is None else audience,
            authorized_party=authorized_party,
            scope=scope,
            groups=groups,
            issued_at=int(time.time()) - age_seconds,
        )

    def agent_token(self, *, owner):
        """The token of an agent's own account: client credentials shape, any client, no username."""
        return self.issuer.access_token(
            "agent-subject",
            audience=self.audience,
            authorized_party="some-agent-client",
            scope=MCP_SCOPE,
            groups=(AGENT_GROUP, MCP_GROUP),
            extra_claims={"agent_owner": owner, "agent_name": "Claude"},
        )

    def call(self, token, tool, arguments, **headers):
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        return self._dialed(self.mcp("POST", token, extra_headers=headers, content=json.dumps(body)))

    def pass_time(self, seconds):
        """Move the app's clock on its own loop, running the timers that fall due."""
        self.client.portal.call(self.scheduler.advance, seconds)

    def mcp(self, method, token, extra_headers=None, **options):
        extra_headers = extra_headers or {}
        authorization = {} if token is None else {"authorization": f"Bearer {token}"}
        return self.client.request(
            method,
            "/mcp",
            headers={
                "content-type": "application/json",
                PROTOCOL_VERSION_HEADER: LATEST_PROTOCOL_VERSION,
                **authorization,
                **extra_headers,
            },
            **options,
        )

    @staticmethod
    def _dialed(response):
        if response.status_code != 200:
            return Dialed(response.status_code, None, None, response)
        result = response.json()["result"]
        return Dialed(200, json.loads(result["content"][0]["text"]), result["isError"], response)


def join_arguments(seat, name="Claude"):
    return {"room": seat.room_id, "name": name}


def admin_groups():
    return (LOGIN_GROUP, ADMIN_GROUP, MCP_GROUP)


@contextmanager
def serving_dialed_call(sign_in: SignInSettings, issuer: FakeIssuer) -> Iterator[DialedCall]:
    stores = SignInStores.in_memory()
    scheduler = ManualScheduler()
    app = create_app(
        sign_in.settings,
        page_build=fixture_page_build(),
        issuer_transports=issuer.transports,
        stores=stores,
        scheduler=scheduler,
        call_features=(ParticipationFeature(sign_in.settings.public_origin),),
    )
    with TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False) as client, ExitStack() as sockets:
        audience = client.get(METADATA_PATH).json()["resource"]
        yield DialedCall(issuer, stores, client, sockets, audience, scheduler)
