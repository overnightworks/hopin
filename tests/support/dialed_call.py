"""HopIn's real composition around participation: sockets on `/ws`, the MCP endpoint, and a fake issuer that signs
the agents' tokens."""

import json
import secrets
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import anyio
from agents.mcp_wire import MCP_ENDPOINT_PATH, PROTOCOL_VERSION, PROTOCOL_VERSION_HEADER
from agents.member import STANDING_URI, PlaceRef, place_window_uri
from fastapi.testclient import TestClient
from mcp_types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY
from webauth.admission import AppRole
from webauth.config import installed_web_auth_config
from webauth.ports import AccountKind

from hopin.agent.mcp import AGENT_GROUP, MCP_CLIENT_ID, MCP_GROUP, MCP_SCOPE, ParticipationFeature
from hopin.agent.tools import CALL_KIND
from hopin.app import create_app
from hopin.config.constants import ADMIN_GROUP, LOGIN_GROUP, WEBSOCKET_PATH, SignalType
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signaling_fakes import ManualScheduler
from tests.support.signed_in import handshake_headers, seed_member
from tests.support.stores import single_process_stores

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from webauth.single_process_stores import SingleProcessStores, StoredAccount

    from hopin.agent.mcp import BuiltParticipationFeature
    from tests.support.fake_issuer import FakeIssuer
    from tests.support.sign_in_settings import SignInSettings

LISTEN_TIMEOUT_SECONDS = 10
METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"


class Seat:
    """One browser's socket: it numbers each frame after the join, as the page does."""

    def __init__(self, socket):
        self._socket = socket
        self._sent = 0
        self.participant_id = None
        self.room_id = None
        self.received = []

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
        the answer to a later frame comes after everything the join does, the offers it sends included. Only the host
        of a call may lock it; the call is left unlocked."""
        self._sent += 1
        self._socket.send_json({"type": SignalType.LOCK_ROOM.value, "seq": self._sent})
        self.frame_of_type(SignalType.ROOM_LOCKED.value)
        self._sent += 1
        self._socket.send_json({"type": SignalType.UNLOCK_ROOM.value, "seq": self._sent})
        self.frame_of_type(SignalType.ROOM_UNLOCKED.value)

    def drop(self, code=1006):
        """Close the socket abnormally, so the seat parks."""
        self._socket.close(code=code)

    def send(self, frame):
        self._sent += 1
        self._socket.send_json({**frame, "seq": self._sent})

    def frame_of_type(self, signal_type):
        """The next frame of this type the server sends, those before it passed over."""
        while True:
            frame = self.next_frame()
            if frame["type"] == signal_type:
                return frame

    def next_frame(self):
        frame = self._socket.receive_json()
        self.received.append(frame)
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
    stores: SingleProcessStores
    client: TestClient
    sockets: ExitStack
    audience: str
    scheduler: ManualScheduler
    participation: BuiltParticipationFeature

    def member(self, *, role=AppRole.USER, subject=None, groups=None):
        """A person with a login. With `groups` the issuer holds those groups for them and their login holds a refresh
        token, so a read of their current groups succeeds."""
        config = installed_web_auth_config(self.client.app)
        name = f"member-{secrets.token_hex(4)}"
        account = self.stores.accounts.create_for_subject(
            f"subject-{name}" if subject is None else subject, name, role=role, kind=AccountKind.PERSON
        )
        refresh_token = None
        if groups is not None:
            self.issuer.grant_groups(account.id, groups)
            refresh_token = self.issuer.refresh_token_for(account.id)
        return account, seed_member(self.stores, config, account=account, refresh_token=refresh_token)

    def another_device(self, account):
        """A further login of the account, which its other logins do not share a session with."""
        return seed_member(self.stores, installed_web_auth_config(self.client.app), account=account)

    def seated(self, **member_options):
        """A person in a call of their own: their account for a token, their login and their seat."""
        account, member = self.member(**member_options)
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

    def agent_token(self, *, owner, subject="agent-subject", agent_name="Claude", preferred_username=None):
        """The token of an agent's own account: client credentials shape, any client; a `preferred_username` is the
        account's username, which the realm sets equal to the subject."""
        username = {} if preferred_username is None else {"preferred_username": preferred_username}
        return self.issuer.access_token(
            subject,
            audience=self.audience,
            authorized_party="some-agent-client",
            scope=MCP_SCOPE,
            groups=(AGENT_GROUP, MCP_GROUP),
            extra_claims={"agent_owner": owner, "agent_name": agent_name, **username},
        )

    def call(self, token, tool, arguments, **headers):
        response = self.request(
            token, "tools/call", {"name": tool, "arguments": arguments}, routing_name=tool, **headers
        )
        return self._dialed(response)

    def request(self, token, method, params=None, *, routing_name=None, **headers):
        """One 2026-07-28 request: the version and capabilities in `_meta`, the routing headers beside it."""
        return self.mcp(
            "POST",
            token,
            extra_headers={**self._routing(method, routing_name), **headers},
            content=self._request_body(method, params),
        )

    def listen(self, token, uris):
        """Open `subscriptions/listen` on the app's ASGI interface, wait for its acknowledgement and disconnect, as a
        client does that closes its stream. A TestClient returns only once the response is complete, which a stream
        never is."""
        method = "subscriptions/listen"
        body = self._request_body(method, {"notifications": {"resourceSubscriptions": list(uris)}})
        headers = {
            **self._mcp_headers(token, self._routing(method, None)),
            "host": urlsplit(str(self.client.base_url)).netloc,
        }
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "root_path": "",
            "scheme": urlsplit(str(self.client.base_url)).scheme,
            "query_string": b"",
            "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
            "client": ("testclient", 50000),
            "server": ("testserver", 443),
            "state": self.client.app_state.copy(),
        }
        self.client.portal.call(self._listen_until_acknowledged, scope, body.encode())

    async def _listen_until_acknowledged(self, scope, body):
        acknowledged, disconnected = anyio.Event(), anyio.Event()
        requested = False

        async def receive():
            nonlocal requested
            if not requested:
                requested = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if b"subscriptions/acknowledged" in message.get("body", b""):
                acknowledged.set()

        with anyio.fail_after(LISTEN_TIMEOUT_SECONDS):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(self.client.app, scope, receive, send)
                await acknowledged.wait()
                disconnected.set()

    @staticmethod
    def _routing(method, routing_name):
        routing = {"mcp-method": method}
        if routing_name is not None:
            routing["mcp-name"] = routing_name
        return routing

    @staticmethod
    def _request_body(method, params):
        meta = {PROTOCOL_VERSION_META_KEY: PROTOCOL_VERSION, CLIENT_CAPABILITIES_META_KEY: {}}
        return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {**(params or {}), "_meta": meta}})

    def standing(self, token):
        """The `MemberStanding` the agent reads at `member://standing`, as its places in a list."""
        response = self.request(token, "resources/read", {"uri": STANDING_URI}, routing_name=STANDING_URI)
        return json.loads(response.json()["result"]["contents"][0]["text"])["standings"]

    def listed(self, token):
        """The URIs `resources/list` answers the token's principal, in the order it answers them."""
        return [resource["uri"] for resource in self.request(token, "resources/list").json()["result"]["resources"]]

    def window(self, token, room_id):
        """The JSON-RPC answer to reading the chat window of the call: its `result` or its `error`."""
        uri = window_uri(room_id)
        return self.request(token, "resources/read", {"uri": uri}, routing_name=uri).json()

    def hearing_tokens(self):
        """The holder of the exchanged tokens, which the MCP route keeps."""
        return self._mcp_front().hearing_tokens

    def shut_down(self):
        """Shut participation down as the app's lifespan does, while the sockets of the test are still open."""
        self.client.portal.call(self._mcp_front().participation().shut_down)

    def _mcp_front(self):
        [route] = [route for route in self.participation.router.routes if route.path == MCP_ENDPOINT_PATH]
        return route.endpoint

    def pass_time(self, seconds):
        """Move the app's clock on its own loop, running the timers that fall due."""
        self.client.portal.call(self.scheduler.advance, seconds)

    def mcp(self, method, token, extra_headers=None, **options):
        # A header set to None is left off the request.
        return self.client.request(method, "/mcp", headers=self._mcp_headers(token, extra_headers), **options)

    @staticmethod
    def _mcp_headers(token, extra_headers=None):
        extra_headers = extra_headers or {}
        authorization = {} if token is None else {"authorization": f"Bearer {token}"}
        headers = {
            "content-type": "application/json",
            PROTOCOL_VERSION_HEADER: PROTOCOL_VERSION,
            "accept": "application/json, text/event-stream",
            **authorization,
            **extra_headers,
        }
        return {name: value for name, value in headers.items() if value is not None}

    @staticmethod
    def _dialed(response):
        if response.status_code != 200:
            return Dialed(response.status_code, None, None, response)
        result = response.json()["result"]
        return Dialed(200, json.loads(result["content"][0]["text"]), result["isError"], response)


class _ParticipationBuilt:
    """The participation feature as the app builds it, keeping what it built."""

    def __init__(self, feature: ParticipationFeature) -> None:
        self._feature = feature
        self.built: BuiltParticipationFeature | None = None

    @property
    def sign_in_scopes(self):
        return self._feature.sign_in_scopes

    @property
    def frame_sources(self):
        return self._feature.frame_sources

    def __call__(self, **parts):
        self.built = self._feature(**parts)
        return self.built


def _no_figure_source(url, timeout_seconds):
    raise AssertionError("this call has no figure source")


def window_uri(room_id):
    return place_window_uri(PlaceRef(kind=CALL_KIND, id=room_id))


def join_arguments(seat, name="Claude"):
    return {"room": seat.room_id, "name": name}


def accept_arguments(standing):
    """The shared arguments of accepting the invitation a read standing carries."""
    invitation = standing["invitation"]
    return {"place": standing["place"], "invitation": invitation["id"], "epoch": invitation["epoch"]}


def admin_groups():
    return (LOGIN_GROUP, ADMIN_GROUP, MCP_GROUP)


@contextmanager
def serving_dialed_call(
    sign_in: SignInSettings,
    issuer: FakeIssuer,
    agent_adders: Mapping[str, str] | None = None,
    *,
    more_call_features=(),
    figure_sources=(),
    figure_transport=None,
    hearing=None,
) -> Iterator[DialedCall]:
    """HopIn serving participation; `figure_transport` is the figures' source, and a figure fetch without one fails the
    test. `hearing` is the speech service agents hear through, none with hearing off."""
    stores = single_process_stores()
    scheduler = ManualScheduler()
    participation = _ParticipationBuilt(
        ParticipationFeature(
            sign_in.settings.public_origin,
            agent_adders or {},
            figure_sources,
            figure_transport or _no_figure_source,
            hearing=hearing,
        )
    )
    app = create_app(
        sign_in.settings,
        page_build=fixture_page_build(),
        issuer_transports=issuer.transports,
        stores=stores,
        scheduler=scheduler,
        call_features=(
            *more_call_features,
            participation,
        ),
    )
    with TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False) as client, ExitStack() as sockets:
        audience = client.get(METADATA_PATH).json()["resource"]
        yield DialedCall(issuer, stores, client, sockets, audience, scheduler, participation.built)
