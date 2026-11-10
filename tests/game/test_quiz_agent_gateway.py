"""The quiz on the client's real `GatewayBackend`: sockets on `/ws`, a fake issuer and a scripted gateway."""

import json
import logging
import secrets
from contextlib import ExitStack
from dataclasses import dataclass

import pytest
import structlog.testing
from agents.agent_file import load_agent
from agents.backend.fake_gateway import FakeGateway, Reply, completion
from agents.backend.gateway import GatewayBackend, GatewayDetail

from hopin.agent.backend import QUIZ_AGENT_FILE
from hopin.game.quiz import QUIZ_TOPICS, question_request
from hopin.game.vocabulary import GamePhase, GameRefusal
from tests.game.support.call_world import IDLE, asked, phase, refused, serving_call
from tests.game.support.world import API_GROUP, agent_settings
from tests.support.fake_issuer import FakeIssuer

pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class QuizScenario:
    """One turn's values; the issuer, subject and fingerprint are the vector agent-runtime's wire.md gives."""

    issuer: str = "https://issuer.example"
    subject: str = "subject-1"
    fingerprint: str = "fp-2db44d96ffdef38f9e1cbfe73e9cf248fb66955b894846b34843a2d3a849d764"
    question: str = "Welcher Fluss fließt durch Lissabon?"
    key_past_the_clients_bound: str = "k" * 1025


SCENARIO = QuizScenario()


@pytest.fixture
def key_file(tmp_path):
    path = tmp_path / "gateway-key"
    path.write_text(f"scenario-key-{secrets.token_hex(16)}\n")
    return path


@pytest.fixture
def quiz_on_gateway(sign_in_settings, key_file):
    """Opens one HopIn whose game runs on a real `GatewayBackend` over a gateway that answers with `replies`."""
    sign_in = sign_in_settings(oidc_issuer=SCENARIO.issuer)
    with ExitStack() as serving:

        def open_quiz(*replies):
            gateway = FakeGateway(*(replies or (completion(SCENARIO.question),)))
            backend = GatewayBackend(agent_settings(key_file).agent, transport=gateway)
            return serving.enter_context(serving_call(sign_in, FakeIssuer(sign_in), backend)), gateway

        yield open_quiz


def refused_frames(reason, starter, *, retry_after_seconds=None):
    return [phase(GamePhase.THINKING, starter), refused(reason, retry_after_seconds), IDLE]


def stops_logged(logs):
    return [log for log in logs if log["event"] == "game_master_turn_refused"]


def first_post_of(gateway):
    [post] = gateway.requests
    return post, json.loads(post.content)


def test_a_member_of_the_api_group_starts_a_quiz_and_every_seat_reads_the_gateways_question(quiz_on_gateway):
    call, gateway = quiz_on_gateway()
    host, guest, _ = call.host_and_guest(call.member(groups=(API_GROUP,)))

    host.start()

    assert host.frames(3) == asked(host, SCENARIO.question)
    assert guest.frames(3) == asked(host, SCENARIO.question)
    assert len(gateway.requests) == 1


def test_the_quiz_offers_the_gateway_no_tools(quiz_on_gateway):
    call, gateway = quiz_on_gateway()
    host, _ = call.seat(call.member(groups=(API_GROUP,)))

    host.start()
    host.frames(3)

    _, body = first_post_of(gateway)
    assert "tools" not in body


def test_a_post_carries_the_agent_files_model_the_quiz_prompt_and_this_quizs_turn_and_nothing_else(quiz_on_gateway):
    """GM-21: A post carries the agent files model the quiz prompt and this quizs turn and nothing else."""
    call, gateway = quiz_on_gateway()
    host, _ = call.seat(call.member(groups=(API_GROUP,)))

    host.start()
    host.frames(3)

    _, body = first_post_of(gateway)
    agent = load_agent(QUIZ_AGENT_FILE, [])
    [system, turn] = body["messages"]
    assert sorted(body) == ["messages", "model", "stream", "user"]
    assert body["model"] == agent.model
    assert system == {"role": "system", "content": agent.prompt}
    assert turn["role"] == "user"
    assert turn["content"] in {question_request(topic) for topic in QUIZ_TOPICS}


def test_the_fingerprint_is_wire_mds_vector_so_the_issuer_goes_out_exactly_as_the_relying_party_binds_it(
    quiz_on_gateway,
):
    """GM-21: The fingerprint is wire mds vector so the issuer goes out exactly as the relying party binds it."""
    call, gateway = quiz_on_gateway()
    host, _ = call.seat(call.member(subject=SCENARIO.subject, groups=(API_GROUP,)))

    host.start()
    host.frames(3)

    _, body = first_post_of(gateway)
    assert body["user"] == SCENARIO.fingerprint


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(completion(SCENARIO.question), id="answered"),
        pytest.param(Reply(status=403), id="refused"),
        pytest.param(Reply(status=500), id="failed"),
    ],
)
def test_the_key_files_line_is_the_bearer_and_stays_out_of_every_log_frame_and_refusal(
    quiz_on_gateway, key_file, reply, caplog
):
    canary = key_file.read_text().strip()
    call, gateway = quiz_on_gateway(reply)
    host, _ = call.seat(call.member(groups=(API_GROUP,)))

    with structlog.testing.capture_logs() as logs, caplog.at_level(logging.DEBUG):
        host.start()
        frames = host.frames(3)

    post, _ = first_post_of(gateway)
    assert post.headers["authorization"] == f"Bearer {canary}"
    assert canary not in json.dumps(frames) + repr(logs) + caplog.text


@pytest.mark.parametrize(
    "groups",
    [
        pytest.param((), id="no-group"),
        pytest.param(("/apps/hopin",), id="another-group"),
        pytest.param((f"{API_GROUP}/below",), id="a-path-below-the-api-group"),
    ],
)
def test_a_starter_outside_the_api_group_is_told_alone_and_nothing_is_posted(quiz_on_gateway, groups):
    """GM-11, GM-19: A starter outside the api group is told alone and nothing is posted."""
    call, gateway = quiz_on_gateway()
    host, guest, _ = call.host_and_guest(call.member(groups=groups))

    host.start()

    assert host.frames(3) == refused_frames(GameRefusal.NO_ACCESS, host)
    assert guest.frames(2) == [phase(GamePhase.THINKING, host), IDLE]
    assert gateway.requests == []


@pytest.mark.parametrize(
    ("headers", "expected_wait"),
    [
        pytest.param({"Retry-After": "7"}, 7, id="seconds"),
        pytest.param({}, 30, id="no-header"),
        pytest.param({"Retry-After": "0"}, 1, id="zero"),
        pytest.param({"Retry-After": "Mon, 02 Nov 2026 12:00:00 GMT"}, 30, id="http-date"),
        pytest.param({"Retry-After": "9" * 5000}, 600, id="five-thousand-digits"),
    ],
)
def test_a_gateway_at_its_limit_is_busy_for_as_long_as_it_says(quiz_on_gateway, headers, expected_wait):
    """GM-13: A gateway at its limit is busy for as long as it says."""
    call, _ = quiz_on_gateway(Reply(status=429, headers=headers))
    host, _ = call.seat(call.member(groups=(API_GROUP,)))

    host.start()

    assert host.frames(3) == refused_frames(GameRefusal.BUSY, host, retry_after_seconds=expected_wait)


@pytest.mark.parametrize(
    ("reply", "detail"),
    [
        pytest.param(Reply(status=422), GatewayDetail.GATEWAY_REFUSED, id="budget-refusal"),
        pytest.param(Reply(status=404), GatewayDetail.GATEWAY_REFUSED, id="another-4xx"),
        pytest.param(Reply(status=302, headers={"Location": "/elsewhere"}), GatewayDetail.GATEWAY_FAILED, id="3xx"),
        pytest.param(Reply(status=503), GatewayDetail.GATEWAY_FAILED, id="5xx"),
        pytest.param(Reply(body=b"{not json"), GatewayDetail.MALFORMED_REPLY, id="malformed-json"),
        pytest.param(completion(""), GatewayDetail.MALFORMED_REPLY, id="empty-text"),
        pytest.param(
            completion(SCENARIO.question, finish_reason="length"), GatewayDetail.MALFORMED_REPLY, id="cut-off"
        ),
    ],
)
def test_a_gateway_that_gives_no_answer_reads_as_unavailable_with_its_closed_detail_logged(
    quiz_on_gateway, reply, detail
):
    call, gateway = quiz_on_gateway(reply)
    host, _ = call.seat(call.member(groups=(API_GROUP,)))

    with structlog.testing.capture_logs() as logs:
        host.start()
        frames = host.frames(3)

    [stopped] = stops_logged(logs)
    assert frames == refused_frames(GameRefusal.UNAVAILABLE, host)
    assert stopped["detail"] == detail.value
    assert len(gateway.requests) == 1


@pytest.mark.parametrize(
    "write_key",
    [
        pytest.param(lambda path: path.unlink(), id="missing"),
        pytest.param(lambda path: path.write_text(""), id="empty"),
        pytest.param(lambda path: path.write_text("one\ntwo\n"), id="two-lines"),
        pytest.param(lambda path: path.write_text(SCENARIO.key_past_the_clients_bound), id="too-long"),
    ],
)
def test_an_unusable_key_file_leaves_the_start_standing_and_makes_the_turn_unavailable_with_no_post(
    quiz_on_gateway, key_file, write_key
):
    write_key(key_file)
    call, gateway = quiz_on_gateway()
    host, _ = call.seat(call.member(groups=(API_GROUP,)))

    with structlog.testing.capture_logs() as logs:
        host.start()
        frames = host.frames(3)

    [stopped] = stops_logged(logs)
    assert frames == refused_frames(GameRefusal.UNAVAILABLE, host)
    assert stopped["detail"] == GatewayDetail.KEY_UNUSABLE.value
    assert gateway.requests == []
