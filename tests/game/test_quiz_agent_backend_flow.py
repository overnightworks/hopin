"""The game master through HopIn's real composition: sockets on `/ws`, a fake issuer and a fake agent backend."""

import asyncio
import json
import secrets
from urllib.parse import urlsplit

import pytest
import structlog.testing
from agent_runtime_client.turn import RejectionCode
from webauth.config import installed_web_auth_config

from hopin.agent.feature import GameFeature
from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import WEBSOCKET_PATH, SignalType
from hopin.game.quiz import QUIZ_TOPICS, question_request
from hopin.game.vocabulary import GAME_RUN_DEADLINE_SECONDS, GamePhase, GameRefusal, GameSignal
from tests.game.support.call_world import IDLE, asked, phase, question, refused, serving_call
from tests.game.support.fake_agent_backend import FakeAgentBackend, Holds, Rejects, Succeeds
from tests.game.support.table_world import answers_in_reveal
from tests.game.support.world import wait_in_a_worker
from tests.support.fake_issuer import FakeIssuer
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signaling_fakes import ManualScheduler
from tests.support.signed_in import handshake_headers, seed_member

pytestmark = pytest.mark.integration


@pytest.fixture
def call(sign_in_settings):
    sign_in = sign_in_settings()
    with serving_call(sign_in, FakeIssuer(sign_in), FakeAgentBackend()) as serving:
        yield serving


def line(text):
    return {"type": GameSignal.GAME_LINE.value, "text": text}


def chat_text_of(frame):
    assert frame["type"] == SignalType.CHAT.value
    return frame["message"]


def revealed(starter, text):
    return [phase(GamePhase.THINKING, starter), line(text)]


def asked_next(starter, text):
    return [phase(GamePhase.THINKING, starter, 2), question(text, 2), phase(GamePhase.ANSWERING, starter, 2)]


def test_a_guests_start_puts_the_question_in_the_hosts_chat_too(call):
    host, guest, _ = call.host_and_guest()

    guest.start()

    assert host.frames(3) == asked(guest)
    assert guest.frames(3) == asked(guest)


def test_the_run_goes_out_for_the_starter_on_the_starters_own_grant_not_the_hosts(call):
    host, guest, guest_member = call.host_and_guest()
    guest_refresh_token = call.stores.sessions.load(guest_member.session.id).refresh_token.get_secret_value()

    guest.start()
    host.frames(3)

    [run] = call.backend.runs
    assert run.principal.sub == guest_member.account.id
    assert call.refresh_tokens_granted() == [guest_refresh_token]


def test_a_second_start_while_the_run_is_held_is_refused_to_its_sender_alone(call):
    host, guest, _ = call.host_and_guest()
    held = Holds()
    call.backend.answer_next(held)

    host.start()
    assert guest.frames(1) == [phase(GamePhase.THINKING, host)]
    assert held.started.wait()
    guest.start()
    assert guest.frames(1) == [refused(GameRefusal.ALREADY_ANSWERING)]
    held.release.set()

    assert host.frames(3) == asked(host)
    assert guest.frames(2) == asked(host)[1:]
    assert len(call.backend.runs) == 1


def test_an_account_without_ai_access_is_told_alone_and_the_others_thinking_turns_idle(call):
    host, guest, _ = call.host_and_guest()
    call.backend.answer_next(Rejects(RejectionCode.NOT_ENABLED))

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
    assert call.backend.runs == []


def test_a_store_failure_while_reading_the_groups_is_logged_by_its_type_alone_and_the_call_goes_on(call, monkeypatch):
    host, guest, _ = call.host_and_guest()
    detail = secrets.token_hex(8)

    def fail_to_store(*_arguments, **_keywords):
        raise ConnectionError(detail)

    monkeypatch.setattr(call.stores.sessions, "replace_refresh_token", fail_to_store)

    with structlog.testing.capture_logs() as logs:
        host.start()
        frames = host.frames(3)
    host.chat("Noch da?")

    assert frames == [phase(GamePhase.THINKING, host), refused(GameRefusal.UNAVAILABLE), IDLE]
    assert {"event": "game_master_raised", "log_level": "error", "error": "ConnectionError"} in logs
    assert detail not in repr(logs)
    assert guest.frames(3)[-1]["message"] == "Noch da?"
    assert call.backend.runs == []


def test_chat_during_a_held_run_reaches_the_others_and_never_the_agent(call):
    host, guest, _ = call.host_and_guest()
    held = Holds()
    call.backend.answer_next(held, Succeeds("Lissabon."))
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

    assert [run for run in call.backend.runs if chat_text in run.request.model_dump_json()] == []


def test_the_reveal_request_carries_each_given_answer_with_its_players_name_and_no_chat_line(call):
    host_member, guest_member, third_member = call.member(), call.member(), call.member()
    host, created = call.seat(host_member)
    guest, _ = call.seat(guest_member, created["room_id"])
    call.seat(third_member, created["room_id"])
    host.frames(2)
    guest.frames(1)
    call.backend.answer_next(Succeeds("Erste Frage?"), Succeeds("Lissabon."), Succeeds("Zweite Frage?"))
    chat_text = f"Plausch {secrets.token_hex(8)}"

    host.start()
    guest.frames(3)
    host.answer("Tejo")
    until_the_first_answer = host.frames(4)
    assert until_the_first_answer[-1]["type"] == GameSignal.GAME_ANSWER_COUNT.value
    guest.answer("Mondego")
    through_the_answers = until_the_first_answer + host.frames(1)
    guest.chat(chat_text)
    through_the_chat = through_the_answers + host.frames(1)
    assert [frame["type"] for frame in through_the_chat][-3:] == [
        GameSignal.GAME_ANSWER_COUNT.value,
        GameSignal.GAME_ANSWER_COUNT.value,
        SignalType.CHAT.value,
    ]
    host.reveal()
    host.frames(5)

    second, third_run = (run.request for run in call.backend.runs[1:])
    [_, _, reveal] = second.turn.messages
    assert reveal.role.value == "user"
    assert answers_in_reveal(reveal.text) == [
        {"name": host_member.account.username, "antwort": "Tejo"},
        {"name": guest_member.account.username, "antwort": "Mondego"},
    ]
    assert [message.text for message in third_run.turn.messages][-3] == reveal.text
    for run in call.backend.runs:
        assert third_member.account.username not in run.request.model_dump_json()
        assert chat_text not in run.request.model_dump_json()


def test_the_reveal_request_holds_the_quiz_so_far_and_an_empty_list_when_nobody_answered(call):
    host_member = call.member()
    host, guest, guest_member = call.host_and_guest(host_member)
    call.backend.answer_next(Succeeds("Erste Frage?"), Succeeds("Lissabon."), Succeeds("Zweite Frage?"))

    host.start()
    guest.frames(3)
    host.reveal()
    host.frames(8)

    first, second, third = (run.request for run in call.backend.runs)
    first_topic = next(topic for topic in QUIZ_TOPICS if second.turn.messages[0].text == question_request(topic))
    assert [(message.role.value, message.text) for message in second.turn.messages[:2]] == [
        ("user", question_request(first_topic)),
        ("assistant", "Erste Frage?"),
    ]
    assert answers_in_reveal(second.turn.messages[2].text) == []
    second_request = third.turn.messages[-1].text
    assert [message.text for message in third.turn.messages][-2] == "Lissabon."
    assert second_request != question_request(first_topic)
    assert any(second_request == question_request(topic) for topic in QUIZ_TOPICS)
    assert second.turn.system == first.turn.system
    assert second.turn.system.startswith("Du bist der Spielleiter")
    names = [host_member.account.username, guest_member.account.username]
    assert [name for name in names if name in second.model_dump_json()] == []


def test_each_chained_run_uses_the_refresh_token_the_run_before_it_rotated_in(call):
    _, guest, guest_member = call.host_and_guest()
    call.backend.answer_next(Succeeds("Erste Frage?"), Succeeds("Lissabon."), Succeeds("Zweite Frage?"))

    def stored_refresh_token():
        return call.stores.sessions.load(guest_member.session.id).refresh_token.get_secret_value()

    seeded = stored_refresh_token()
    guest.start()
    guest.frames(3)
    after_question = stored_refresh_token()
    guest.reveal()

    assert guest.frames(5) == [*revealed(guest, "Lissabon."), *asked_next(guest, "Zweite Frage?")]
    granted = call.refresh_tokens_granted()
    assert granted[:2] == [seeded, after_question]
    assert len(set(granted)) == 3
    assert stored_refresh_token() not in granted


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


class OneLoopServer:
    """One event loop serves the lifespan and the socket here, as the server's does; the test client
    would run each socket on a loop of its own, whose end cancels the run before a shutdown could."""

    def __init__(self, sign_in_settings):
        sign_in = sign_in_settings()
        self.issuer = FakeIssuer(sign_in)
        self.backend = FakeAgentBackend()
        self.scheduler = ManualScheduler()
        self._stores = SignInStores.in_memory()
        self.app = create_app(
            sign_in.settings,
            page_build=fixture_page_build(),
            issuer_transports=self.issuer.transports,
            stores=self._stores,
            scheduler=self.scheduler,
            call_feature=GameFeature(self.backend),
        )
        self._to_app, self._from_app = asyncio.Queue(), asyncio.Queue()
        self._connection = None

    def _member(self):
        config = installed_web_auth_config(self.app)
        account = self._stores.accounts.create_for_subject("subject-anna", "anna", role=config.user_role)
        return seed_member(
            self._stores, config, account=account, refresh_token=self.issuer.refresh_token_for(account.id)
        )

    async def start_a_quiz(self):
        scope = socket_scope(self._member())
        self._connection = asyncio.create_task(self.app(scope, self._to_app.get, self._from_app.put))
        await self._to_app.put({"type": "websocket.connect"})
        assert (await self._from_app.get())["type"] == "websocket.accept"
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps({"type": SignalType.JOIN.value})})
        await self._from_app.get()
        game_start = {"type": GameSignal.GAME_START.value, "seq": 1}
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(game_start)})

    async def leave(self):
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        await self._connection


@pytest.mark.asyncio
async def test_shutting_down_mid_run_cancels_the_run_and_waits_for_it(sign_in_settings):
    server = OneLoopServer(sign_in_settings)
    held = Holds()
    server.backend.answer_next(held)

    with structlog.testing.capture_logs() as logs:
        async with server.app.router.lifespan_context(server.app):
            await server.start_a_quiz()
            await held.started.wait_async()
        runs_logged = [log["outcome"] for log in logs if log["event"] == "game_master_run"]
    [run] = server.backend.runs
    run_closed_at_shutdown = run.closed.is_set()
    await server.leave()

    assert run_closed_at_shutdown
    assert runs_logged == [GameRefusal.UNAVAILABLE.value]


@pytest.mark.asyncio
async def test_the_run_deadline_during_a_held_refresh_grant_ends_the_run_unavailable_with_no_turn(sign_in_settings):
    server = OneLoopServer(sign_in_settings)
    hold = server.issuer.hold("refresh_token")

    with structlog.testing.capture_logs() as logs:
        async with server.app.router.lifespan_context(server.app):
            await server.start_a_quiz()
            await wait_in_a_worker(hold.parked)
            await server.scheduler.advance(GAME_RUN_DEADLINE_SECONDS)
            hold.release.set()
            await wait_in_a_worker(hold.answered)
            await server.leave()
        runs_logged = [log["outcome"] for log in logs if log["event"] == "game_master_run"]

    assert runs_logged == [GameRefusal.UNAVAILABLE.value]
    assert server.backend.runs == []
