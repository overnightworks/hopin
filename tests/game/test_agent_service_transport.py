import asyncio
import secrets
import socket

import pytest
import structlog.testing
from agent_runtime_client.sse import encode_event
from agent_runtime_client.wire import PROTOCOL_VERSION, RejectionCode, RunRejection, RunStarted, Succeeded

from hopin.game import AGENT_SERVICE_OVER_HTTP
from hopin.game.table import Line, Refused
from hopin.game.vocabulary import GameRefusal
from tests.game.support.world import OPENING, build_world, cancelled, wait_in_a_worker
from tests.support.loopback_origin import (
    BEARER_ECHO,
    LOCALHOST,
    HeldStream,
    Origin,
    http_response,
    streamed_head,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

RUNS_PATH = "/runs"
RUN_ID = "0b6f9a52-3c41-4f0e-9d5c-1f2a3b4c5d6e"
# Far more than the kernel's socket buffers hold, so an origin that sends all of it was read to the end.
OVERSIZED_BYTES = 32 * 1024 * 1024


@pytest.fixture
def origin():
    loopback = Origin()
    yield loopback
    loopback.close()


@pytest.fixture
def world(sign_in_settings):
    return build_world(sign_in_settings())


def held_run(question):
    started = RunStarted(v=PROTOCOL_VERSION, run_id=RUN_ID, seq=0, heartbeat_interval_s=15)
    succeeded = Succeeded(v=PROTOCOL_VERSION, run_id=RUN_ID, seq=1, text=question)
    return HeldStream(first=streamed_head("text/event-stream") + encode_event(started), rest=encode_event(succeeded))


def over_http(world, origin):
    return world.game_master_over(AGENT_SERVICE_OVER_HTTP, agent_service_url=origin.base_url)


async def start_held(game_master, starter, stream):
    run = asyncio.create_task(game_master.ask(starter, OPENING))
    await wait_in_a_worker(stream.started)
    return run


async def test_cancelling_a_held_run_closes_its_connection(world, origin):
    stream = held_run("Frage?")
    origin.serve_streams(RUNS_PATH, stream)
    starter, _ = world.starter()
    run = await start_held(over_http(world, origin), starter, stream)

    await cancelled(run)

    await wait_in_a_worker(stream.peer_closed)


@pytest.mark.parametrize("end_of_the_first", ["finished", "cancelled"])
async def test_the_end_of_one_rooms_run_leaves_the_other_rooms_stream_open(world, origin, end_of_the_first):
    first, second = held_run("Erste Frage?"), held_run("Zweite Frage?")
    origin.serve_streams(RUNS_PATH, first, second)
    game_master = over_http(world, origin)
    first_starter, _ = world.starter("anna")
    second_starter, _ = world.starter("ben")
    first_run = await start_held(game_master, first_starter, first)
    second_run = await start_held(game_master, second_starter, second)

    if end_of_the_first == "finished":
        first.release.set()
        assert await first_run == Line("Erste Frage?")
    else:
        await cancelled(first_run)
    second.release.set()

    assert await second_run == Line("Zweite Frage?")


async def test_a_redirect_is_not_followed(world, origin):
    origin.serve(RUNS_PATH, http_response("307 Temporary Redirect", b"", extra_header=b"Location: /elsewhere\r\n"))
    origin.serve("/elsewhere", http_response("200 OK", b""))
    starter, _ = world.starter()

    outcome = await over_http(world, origin).ask(starter, OPENING)

    assert outcome == Refused(GameRefusal.UNAVAILABLE)
    assert origin.paths_requested() == [RUNS_PATH]


async def test_a_request_whose_connection_drops_before_the_answer_is_sent_once_and_unavailable(world, origin):
    origin.serve(RUNS_PATH, b"")
    starter, _ = world.starter()

    outcome = await over_http(world, origin).ask(starter, OPENING)

    assert outcome == Refused(GameRefusal.UNAVAILABLE)
    assert (origin.connections, origin.paths_requested()) == (1, [RUNS_PATH])


async def test_a_refused_connection_is_unavailable_and_logged_by_its_error_type(world):
    with socket.socket() as unused:
        unused.bind((LOCALHOST, 0))
        port = unused.getsockname()[1]
    starter, _ = world.starter()
    game_master = world.game_master_over(AGENT_SERVICE_OVER_HTTP, agent_service_url=f"http://{LOCALHOST}:{port}")

    with structlog.testing.capture_logs() as logs:
        outcome = await game_master.ask(starter, OPENING)

    assert outcome == Refused(GameRefusal.UNAVAILABLE)
    assert [log for log in logs if log["event"] == "agent_service_refused"] == [
        {"event": "agent_service_refused", "log_level": "info", "error": "ConnectError"}
    ]


def refusals(logs):
    return [log for log in logs if log["event"] == "agent_service_refused"]


def bearer_sent_to(origin):
    [(_, _, headers, _)] = origin.requests
    return headers["Authorization"].removeprefix("Bearer ")


async def test_the_answers_status_line_and_headers_reach_no_log(world, origin, stdlib_records):
    sentinel = f"Sentinel-{secrets.token_hex(8)}"
    echo = f"{BEARER_ECHO.decode()} {sentinel}"
    rejection = RunRejection(v=PROTOCOL_VERSION, code=RejectionCode.UNAVAILABLE, message="refused")
    origin.serve(
        RUNS_PATH,
        http_response(f"503 {echo}", rejection.model_dump_json().encode(), extra_header=f"X-Echo: {echo}\r\n".encode()),
    )
    starter, _ = world.starter()

    with structlog.testing.capture_logs() as logs:
        outcome = await over_http(world, origin).ask(starter, OPENING)

    shown = repr(logs) + "".join(stdlib_records)
    assert outcome == Refused(GameRefusal.UNAVAILABLE)
    for secret in (bearer_sent_to(origin), sentinel):
        assert secret not in shown
    assert refusals(logs) == [
        {"event": "agent_service_refused", "log_level": "info", "status": 503, "code": "unavailable"}
    ]


async def test_an_answer_the_transport_cannot_read_is_unavailable_and_logged_by_its_error_type_only(
    world, origin, stdlib_records
):
    sentinel = f"Sentinel-{secrets.token_hex(8)}"
    origin.serve(RUNS_PATH, b"HTTP/1.1 5x3 " + BEARER_ECHO + b" " + sentinel.encode() + b"\r\n\r\n")
    starter, _ = world.starter()

    with structlog.testing.capture_logs() as logs:
        outcome = await over_http(world, origin).ask(starter, OPENING)

    shown = repr(logs) + "".join(stdlib_records)
    assert outcome == Refused(GameRefusal.UNAVAILABLE)
    for secret in (bearer_sent_to(origin), sentinel):
        assert secret not in shown
    assert refusals(logs) == [{"event": "agent_service_refused", "log_level": "info", "error": "RemoteProtocolError"}]


async def test_an_oversized_rejection_is_closed_unread_and_unavailable(world):
    origin = Origin()
    origin.serve(
        RUNS_PATH,
        http_response(
            "503 Service Unavailable", b" " * OVERSIZED_BYTES, extra_header=b"Content-Type: application/json\r\n"
        ),
    )
    starter, _ = world.starter()
    try:
        with structlog.testing.capture_logs() as logs:
            outcome = await over_http(world, origin).ask(starter, OPENING)
    finally:
        await asyncio.to_thread(origin.close)

    assert outcome == Refused(GameRefusal.UNAVAILABLE)
    assert origin.cut_short == [RUNS_PATH]
    assert refusals(logs) == [
        {"event": "agent_service_refused", "log_level": "info", "status": 503, "body": "oversized"}
    ]
