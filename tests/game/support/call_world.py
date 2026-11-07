"""HopIn's real composition around a quiz: sockets on `/ws`, a fake issuer and whichever agent backend a test names."""

import secrets
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
from webauth.admission import AppRole
from webauth.config import installed_web_auth_config

from hopin.agent.feature import GameFeature
from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import WEBSOCKET_PATH, SignalType
from hopin.game.quiz import QUIZ_ROUNDS
from hopin.game.vocabulary import GamePhase, GameSignal
from tests.game.support.fake_agent_backend import DEFAULT_QUESTION
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signaling_fakes import ManualScheduler
from tests.support.signed_in import cookie_header, handshake_headers, seed_member

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agent_runtime_client.backend import AgentBackend

    from hopin.agent.settings import SpeechSettings
    from tests.support.fake_issuer import FakeIssuer
    from tests.support.sign_in_settings import SignInSettings


class Seat:
    """One browser's socket: it numbers each frame after the join, as the page does."""

    def __init__(self, socket):
        self._socket = socket
        self._sent = 0
        self.participant_id = None

    def join(self, room_id=None):
        join = {"type": SignalType.JOIN.value}
        if room_id is not None:
            join["room_id"] = room_id
        self._socket.send_json(join)
        joined = self._socket.receive_json()
        self.participant_id = joined["participant_id"]
        return joined

    def send(self, frame):
        self._sent += 1
        self._socket.send_json({**frame, "seq": self._sent})

    def start(self):
        self.send({"type": GameSignal.GAME_START.value})

    def reveal(self):
        self.send({"type": GameSignal.GAME_REVEAL.value})

    def answer(self, text):
        self.send({"type": GameSignal.GAME_ANSWER.value, "text": text})

    def chat(self, text):
        self.send({"type": SignalType.CHAT.value, "message": text})

    def frames(self, count):
        """The next `count` frames, without their replay sequence number."""
        received = [self._socket.receive_json() for _ in range(count)]
        return [{key: value for key, value in frame.items() if key != "seq"} for frame in received]


@dataclass
class Call:
    """A HopIn instance whose issuer is a fake, and a way to seat its members."""

    issuer: FakeIssuer
    backend: AgentBackend
    stores: SignInStores
    client: TestClient
    sockets: ExitStack

    def member(self, *, subject=None, groups=(), with_refresh_token=True):
        config = installed_web_auth_config(self.client.app)
        name = f"member-{secrets.token_hex(4)}"
        account = self.stores.accounts.create_for_subject(subject or f"subject-{name}", name, role=AppRole.USER)
        self.issuer.grant_groups(account.id, groups)
        refresh_token = self.issuer.refresh_token_for(account.id) if with_refresh_token else None
        return seed_member(self.stores, config, account=account, refresh_token=refresh_token)

    def seat(self, member, room_id=None):
        socket = self.sockets.enter_context(
            self.client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(member))
        )
        seat = Seat(socket)
        return seat, seat.join(room_id)

    def host_and_guest(self, host_member=None):
        host, created = self.seat(self.member() if host_member is None else host_member)
        guest_member = self.member()
        guest, _ = self.seat(guest_member, created["room_id"])
        host.frames(1)
        return host, guest, guest_member

    def voice(self, member, path):
        """What a seat's browser gets fetching the voice at the path its frame names, on its session cookie."""
        return self.client.get(path, headers={"cookie": cookie_header(member.set_cookie_headers)})

    def grant_types(self):
        return [request.form["grant_type"] for request, _ in self.issuer.exchanges]

    def refresh_tokens_granted(self):
        return [
            request.form["refresh_token"]
            for request, _ in self.issuer.exchanges
            if request.form["grant_type"] == "refresh_token"
        ]


@contextmanager
def serving_call(
    sign_in: SignInSettings, issuer: FakeIssuer, backend: AgentBackend, speech: SpeechSettings | None = None
) -> Iterator[Call]:
    stores = SignInStores.in_memory()
    app = create_app(
        sign_in.settings,
        page_build=fixture_page_build(),
        issuer_transports=issuer.transports,
        stores=stores,
        scheduler=ManualScheduler(),
        call_features=(GameFeature(backend) if speech is None else GameFeature(backend, speech),),
    )
    with TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False) as client, ExitStack() as sockets:
        yield Call(issuer, backend, stores, client, sockets)


IDLE = {"type": GameSignal.GAME_PHASE.value, "phase": GamePhase.IDLE.value}


def phase(game_phase, starter, round_number=1):
    return {
        "type": GameSignal.GAME_PHASE.value,
        "phase": game_phase.value,
        "round": round_number,
        "rounds": QUIZ_ROUNDS,
        "starter": starter.participant_id,
    }


def question(text=DEFAULT_QUESTION, round_number=1, *, voiced=False):
    frame = {"type": GameSignal.GAME_LINE.value, "text": text, "round": round_number}
    return {**frame, "voiced": True} if voiced else frame


def refused(reason, retry_after_seconds=None):
    frame = {"type": GameSignal.GAME_REFUSED.value, "reason": reason.value}
    if retry_after_seconds is not None:
        frame["retry_after_seconds"] = retry_after_seconds
    return frame


def asked(starter, text=DEFAULT_QUESTION, *, voiced=False):
    return [phase(GamePhase.THINKING, starter), question(text, voiced=voiced), phase(GamePhase.ANSWERING, starter)]
