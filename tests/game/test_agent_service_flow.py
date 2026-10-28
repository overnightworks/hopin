"""The game master through HopIn's real composition: sockets on `/ws`, a fake issuer and a fake agent service."""

import asyncio
import json
import secrets
from contextlib import ExitStack
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urlsplit

import pytest
import structlog.testing
from agent_runtime_client.wire import RejectionCode
from fastapi.testclient import TestClient
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import WEBSOCKET_PATH, SignalType
from hopin.game import GameSettings, game_feature
from hopin.game.quiz import NEXT_QUESTION_REQUEST, QUIZ_ROUNDS, REVEAL_REQUEST
from hopin.game.vocabulary import GamePhase, GameRefusal, GameSignal
from tests.game.support.fake_agent_service import DEFAULT_QUESTION, FakeAgentService, Holds, Rejects, Succeeds
from tests.support.fake_issuer import FakeIssuer
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signaling_fakes import ManualScheduler
from tests.support.signed_in import handshake_headers, seed_member

pytestmark = pytest.mark.integration


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

    def chat(self, text):
        self.send({"type": SignalType.CHAT.value, "message": text})

    def frames(self, count):
        """The next `count` frames, without their replay sequence number."""
        received = [self._socket.receive_json() for _ in range(count)]
        return [{key: value for key, value in frame.items() if key != "seq"} for frame in received]


@dataclass
class Call:
    """A HopIn instance whose agent service and issuer are fakes, and a way to seat its members."""

    issuer: FakeIssuer
    service: FakeAgentService
    stores: SignInStores
    client: TestClient
    sockets: ExitStack

    def member(self, *, with_refresh_token=True):
        config = installed_web_auth_config(self.client.app)
        name = f"member-{secrets.token_hex(4)}"
        account = self.stores.accounts.create_for_subject(f"subject-{name}", name, role=config.user_role)
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

    def refresh_tokens_granted(self):
        return [
            request.form["refresh_token"]
            for request, _ in self.issuer.exchanges
            if request.form["grant_type"] == "refresh_token"
        ]


@pytest.fixture
def call(sign_in_settings):
    sign_in = sign_in_settings()
    issuer = FakeIssuer(sign_in)
    service = FakeAgentService(issuer)
    stores = SignInStores.in_memory()
    app = create_app(
        sign_in.settings,
        issuer_transports=issuer.transports,
        stores=stores,
        scheduler=ManualScheduler(),
        call_feature=game_feature(GameSettings(), transport=service.transport),
    )
    with TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False) as client, ExitStack() as sockets:
        yield Call(issuer, service, stores, client, sockets)


IDLE = {"type": GameSignal.GAME_PHASE.value, "phase": GamePhase.IDLE.value}


def phase(game_phase, starter):
    return {
        "type": GameSignal.GAME_PHASE.value,
        "phase": game_phase.value,
        "round": 1,
        "rounds": QUIZ_ROUNDS,
        "starter": starter.participant_id,
    }


def question(text=DEFAULT_QUESTION):
    return {"type": GameSignal.GAME_LINE.value, "text": text, "round": 1}


def line(text):
    return {"type": GameSignal.GAME_LINE.value, "text": text}


def refused(reason):
    return {"type": GameSignal.GAME_REFUSED.value, "reason": reason.value}


def chat_text_of(frame):
    assert frame["type"] == SignalType.CHAT.value
    return frame["message"]


def asked(starter, text=DEFAULT_QUESTION):
    return [phase(GamePhase.THINKING, starter), question(text), phase(GamePhase.ANSWERING, starter)]


def revealed(starter, text):
    return [phase(GamePhase.THINKING, starter), line(text), IDLE]


def test_a_guests_start_puts_the_question_in_the_hosts_chat_too(call):
    host, guest, _ = call.host_and_guest()

    guest.start()

    assert host.frames(3) == asked(guest)
    assert guest.frames(3) == asked(guest)


def test_the_run_goes_out_on_the_starters_token_pair_not_the_hosts(call):
    host, guest, guest_member = call.host_and_guest()
    guest_refresh_token = call.stores.sessions.load(guest_member.session.id).refresh_token.get_secret_value()

    guest.start()
    host.frames(3)

    [run] = call.service.runs
    assert (run.subject, run.workload_admitted) == (guest_member.account.id, True)
    assert call.refresh_tokens_granted() == [guest_refresh_token]


def test_a_second_start_while_the_run_is_held_is_refused_to_its_sender_alone(call):
    host, guest, _ = call.host_and_guest()
    held = Holds()
    call.service.answer_next(held)

    host.start()
    assert guest.frames(1) == [phase(GamePhase.THINKING, host)]
    assert held.started.wait()
    guest.start()
    assert guest.frames(1) == [refused(GameRefusal.ALREADY_ANSWERING)]
    held.release.set()

    assert host.frames(3) == asked(host)
    assert guest.frames(2) == asked(host)[1:]
    assert len(call.service.runs) == 1


def test_an_account_without_ai_access_is_told_alone_and_the_others_thinking_turns_idle(call):
    host, guest, _ = call.host_and_guest()
    call.service.answer_next(Rejects(HTTPStatus.FORBIDDEN, RejectionCode.NOT_ENABLED))

    host.start()

    assert host.frames(3) == [phase(GamePhase.THINKING, host), refused(GameRefusal.NO_ACCESS), IDLE]
    assert guest.frames(2) == [phase(GamePhase.THINKING, host), IDLE]


def test_a_session_without_a_refresh_token_is_asked_to_sign_in_again_and_the_call_goes_on(call):
    host, guest, _ = call.host_and_guest(call.member(with_refresh_token=False))

    host.start()
    assert host.frames(3) == [phase(GamePhase.THINKING, host), refused(GameRefusal.SIGN_IN_AGAIN), IDLE]
    assert guest.frames(2) == [phase(GamePhase.THINKING, host), IDLE]
    host.chat("Noch da?")

    assert chat_text_of(guest.frames(1)[0]) == "Noch da?"
    assert call.service.runs == []


def test_chat_during_a_held_run_reaches_the_others_and_never_the_agent_service(call):
    host, guest, _ = call.host_and_guest()
    held = Holds()
    call.service.answer_next(held, Succeeds("Lissabon."))
    chat_text = f"Meine Antwort {secrets.token_hex(8)}"

    host.start()
    assert host.frames(1) == [phase(GamePhase.THINKING, host)]
    assert held.started.wait()
    guest.chat(chat_text)
    assert chat_text_of(host.frames(1)[0]) == chat_text
    held.release.set()
    guest.frames(3)
    host.reveal()
    host.frames(5)

    assert [run for run in call.service.runs if chat_text in run.request.model_dump_json()] == []


def test_the_reveal_request_holds_the_quiz_so_far_and_no_name(call):
    host_member = call.member()
    host, guest, guest_member = call.host_and_guest(host_member)
    call.service.answer_next(Succeeds("Erste Frage?"), Succeeds("Lissabon."))

    host.start()
    guest.frames(3)
    host.reveal()
    host.frames(6)

    first, second = (run.request for run in call.service.runs)
    assert [(message.role.value, message.text) for message in second.turn.messages] == [
        ("user", NEXT_QUESTION_REQUEST),
        ("assistant", "Erste Frage?"),
        ("user", REVEAL_REQUEST),
    ]
    assert second.turn.system == first.turn.system
    assert second.turn.system.startswith("Du bist der Spielleiter")
    names = [host_member.account.username, guest_member.account.username]
    assert [name for name in names if name in second.model_dump_json()] == []


def test_the_starters_reveal_uses_the_refresh_token_the_question_run_rotated_in(call):
    _, guest, guest_member = call.host_and_guest()
    call.service.answer_next(Succeeds("Erste Frage?"), Succeeds("Lissabon."))

    def stored_refresh_token():
        return call.stores.sessions.load(guest_member.session.id).refresh_token.get_secret_value()

    seeded = stored_refresh_token()
    guest.start()
    guest.frames(3)
    rotated = stored_refresh_token()
    guest.reveal()

    assert guest.frames(3) == revealed(guest, "Lissabon.")
    assert rotated != seeded
    assert call.refresh_tokens_granted() == [seeded, rotated]


def test_alone_in_the_call_the_host_gets_the_question(call):
    host, _ = call.seat(call.member())

    host.start()

    assert host.frames(3) == asked(host)


def socket_scope(member):
    """The scope of a socket handshake to `/ws`, as a server hands it to the app."""
    host = urlsplit(TEST_PUBLIC_ORIGIN).netloc
    headers = {"host": host, **handshake_headers(member)}
    return {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "wss",
        "path": WEBSOCKET_PATH,
        "raw_path": WEBSOCKET_PATH.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(name.encode(), value.encode()) for name, value in headers.items()],
        "client": ("127.0.0.1", 50000),
        "server": (host, 443),
        "subprotocols": [],
        "state": {},
    }


@pytest.mark.asyncio
async def test_shutting_down_mid_run_cancels_the_run_and_waits_for_it(sign_in_settings):
    # One event loop serves the lifespan and the socket here, as the server's does; the test client
    # would run each socket on a loop of its own, whose end cancels the run before the shutdown could.
    sign_in = sign_in_settings()
    issuer = FakeIssuer(sign_in)
    service = FakeAgentService(issuer)
    stores = SignInStores.in_memory()
    held = Holds()
    service.answer_next(held)
    app = create_app(
        sign_in.settings,
        issuer_transports=issuer.transports,
        stores=stores,
        scheduler=ManualScheduler(),
        call_feature=game_feature(GameSettings(), transport=service.transport),
    )
    config = installed_web_auth_config(app)
    account = stores.accounts.create_for_subject("subject-anna", "anna", role=config.user_role)
    member = seed_member(stores, config, account=account, refresh_token=issuer.refresh_token_for(account.id))
    to_app, from_app = asyncio.Queue(), asyncio.Queue()

    with structlog.testing.capture_logs() as logs:
        async with app.router.lifespan_context(app):
            connection = asyncio.create_task(app(socket_scope(member), to_app.get, from_app.put))
            await to_app.put({"type": "websocket.connect"})
            assert (await from_app.get())["type"] == "websocket.accept"
            await to_app.put({"type": "websocket.receive", "text": json.dumps({"type": SignalType.JOIN.value})})
            await from_app.get()
            game_start = {"type": GameSignal.GAME_START.value, "seq": 1}
            await to_app.put({"type": "websocket.receive", "text": json.dumps(game_start)})
            await held.started.wait_async()
        runs_logged = [log["outcome"] for log in logs if log["event"] == "game_master_run"]
    [run] = service.runs
    run_closed_at_shutdown = run.closed.is_set()
    await to_app.put({"type": "websocket.disconnect", "code": 1000})
    await connection

    assert run_closed_at_shutdown
    assert runs_logged == [GameRefusal.UNAVAILABLE.value]
