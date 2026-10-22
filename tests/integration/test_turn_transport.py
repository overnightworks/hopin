import json
import logging
import secrets
import socket
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
import urllib3
from pydantic import SecretStr

from hopin.web.ice_servers import TurnRelayFailure, TurnRelayUnavailableError
from hopin.web.turn_relay import CloudflareTurnRelay, TurnAnswer, post_over_https

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.integration

LOCALHOST = "127.0.0.1"
KEY_PATH = "/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
ARRIVAL_CAP_SECONDS = 10
SHORT_TIMEOUT = urllib3.Timeout(connect=2, read=ARRIVAL_CAP_SECONDS)
PROVIDER_BODY = json.dumps(
    {
        "iceServers": [
            {"urls": ["stun:stun.example.test:3478"]},
            {"urls": ["turn:turn.example.test:3478"], "username": "user", "credential": "secret"},
        ]
    }
).encode()


def http_response(
    status_line: str, body: bytes, *, extra_header: bytes = b"", declared_length: int | None = None
) -> bytes:
    length = len(body) if declared_length is None else declared_length
    head = f"HTTP/1.1 {status_line}\r\nContent-Length: {length}\r\nConnection: close\r\n".encode()
    return head + extra_header + b"\r\n" + body


@dataclass
class Origin:
    """A loopback HTTP origin: it answers each path from a script and can hold an answer until released."""

    requests: list[tuple[str, str, dict[str, str], bytes]] = field(default_factory=list)
    received: dict[str, threading.Event] = field(default_factory=dict)
    release: dict[str, threading.Event] = field(default_factory=dict)
    scripts: dict[str, bytes] = field(default_factory=dict)
    listener: socket.socket = field(default_factory=socket.socket)
    threads: list[threading.Thread] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.listener.bind((LOCALHOST, 0))
        self.listener.listen()
        self.port = self.listener.getsockname()[1]
        self._accepting = threading.Thread(target=self._accept)
        self._accepting.start()

    def serve(self, path: str, answer: bytes, *, held: bool = False) -> str:
        self.scripts[path] = answer
        self.received[path] = threading.Event()
        self.release[path] = threading.Event()
        if not held:
            self.release[path].set()
        return f"http://{LOCALHOST}:{self.port}{path}"

    def paths_requested(self) -> list[str]:
        return [path for _, path, _, _ in self.requests]

    def close(self) -> None:
        for release in self.release.values():
            release.set()
        self.listener.shutdown(socket.SHUT_RDWR)
        self.listener.close()
        self._accepting.join()
        for thread in self.threads:
            thread.join()

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            thread = threading.Thread(target=self._answer, args=(connection,))
            self.threads.append(thread)
            thread.start()

    def _answer(self, connection: socket.socket) -> None:
        with connection:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                head += chunk
            head, _, body = head.partition(b"\r\n\r\n")
            method, path, _ = head.split(b"\r\n")[0].decode().split(" ")
            headers = dict(line.decode().split(": ", 1) for line in head.split(b"\r\n")[1:])
            while len(body) < int(headers.get("Content-Length", 0)):
                body += connection.recv(4096)
            self.requests.append((method, path, headers, body))
            if path not in self.scripts:
                connection.sendall(http_response("404 Not Found", b""))
                return
            self.received[path].set()
            self.release[path].wait(ARRIVAL_CAP_SECONDS)
            try:
                connection.sendall(self.scripts[path])
            except OSError:
                return


@pytest.fixture
def origin() -> Iterator[Origin]:
    loopback = Origin()
    yield loopback
    loopback.close()


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.NOTSET)
        self.rendered: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.rendered.append(logging.Formatter().format(record))


@pytest.fixture
def urllib3_records() -> Iterator[list[str]]:
    handler = ListHandler()
    urllib3_logger = logging.getLogger("urllib3")
    previous_level = urllib3_logger.level
    logging.getLogger().addHandler(handler)
    urllib3_logger.setLevel(logging.DEBUG)
    yield handler.rendered
    urllib3_logger.setLevel(previous_level)
    logging.getLogger().removeHandler(handler)


def unique_key_path() -> tuple[str, str]:
    key_id = secrets.token_hex(16)
    return key_id, KEY_PATH.format(key_id=key_id)


def post(url: str, token: str, timeout: urllib3.Timeout = SHORT_TIMEOUT) -> TurnAnswer:
    return post_over_https(url, headers={"Authorization": f"Bearer {token}"}, body=b'{"ttl": 43200}', timeout=timeout)


def relay_toward(url: str, key_id: str, token: str) -> CloudflareTurnRelay:
    def post_to_the_origin(_url, *, headers, body, timeout):
        return post_over_https(url, headers=headers, body=body, timeout=timeout)

    return CloudflareTurnRelay(key_id=key_id, api_token=SecretStr(token), post=post_to_the_origin)


def failure_of(call: Callable[[], object]) -> TurnRelayUnavailableError:
    with pytest.raises(TurnRelayUnavailableError) as failure:
        call()
    return failure.value


def rendered_traceback(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


def malformed_header_line(sentinel: str) -> bytes:
    return f"X-Broken {sentinel}\r\n".encode()


def answer_with_a_broken_header() -> bytes:
    return http_response("200 OK", b"{}", extra_header=malformed_header_line(secrets.token_hex(8)))


class TestTransport:
    def test_a_2xx_answer_comes_back_with_its_status_and_body_and_the_origin_saw_one_post(
        self, origin, urllib3_records
    ):
        key_id, path = unique_key_path()
        token = secrets.token_urlsafe(32)
        url = origin.serve(path, http_response("201 Created", PROVIDER_BODY))

        servers = relay_toward(url, key_id, token).relay_servers()

        [(method, seen_path, headers, body)] = origin.requests
        assert (method, seen_path, headers["Authorization"], body) == (
            "POST",
            path,
            f"Bearer {token}",
            b'{"ttl": 43200}',
        )
        assert [server.urls for server in servers] == [["turn:turn.example.test:3478"]]
        assert not any(key_id in line or token in line for line in urllib3_records)

    def test_the_status_and_body_come_back_unchanged(self, origin):
        url = origin.serve("/answer", http_response("201 Created", b"body"))
        assert post(url, "token") == TurnAnswer(201, b"body")

    def test_a_500_is_refused_with_its_status_after_one_request_and_leaks_nothing(self, origin, urllib3_records):
        key_id, path = unique_key_path()
        token = secrets.token_urlsafe(32)
        url = origin.serve(path, http_response("500 Internal Server Error", b""))

        error = failure_of(relay_toward(url, key_id, token).relay_servers)

        assert (error.reason, error.status, len(origin.requests)) == (TurnRelayFailure.REFUSED, 500, 1)
        assert key_id not in rendered_traceback(error) + str(error) + "".join(urllib3_records)
        assert token not in rendered_traceback(error) + str(error) + "".join(urllib3_records)

    def test_a_redirect_is_refused_and_not_followed(self, origin):
        key_id, path = unique_key_path()
        url = origin.serve(path, http_response("302 Found", b"", extra_header=b"Location: /second\r\n"))
        origin.serve("/second", http_response("200 OK", b""))

        error = failure_of(relay_toward(url, key_id, "token").relay_servers)

        assert (error.reason, error.status, origin.paths_requested()) == (TurnRelayFailure.REFUSED, 302, [path])

    def test_an_origin_that_never_answers_times_out_within_the_read_timeout(self, origin):
        url = origin.serve("/silent", http_response("200 OK", b""), held=True)
        started = time.monotonic()

        error = failure_of(lambda: post(url, "token", urllib3.Timeout(connect=2, read=0.3)))

        assert error.reason is TurnRelayFailure.TIMED_OUT
        assert time.monotonic() - started < ARRIVAL_CAP_SECONDS

    def test_nothing_listening_is_unreachable_after_one_attempt(self):
        with socket.socket() as unused:
            unused.bind((LOCALHOST, 0))
            port = unused.getsockname()[1]
        assert failure_of(lambda: post(f"http://{LOCALHOST}:{port}/x", "token")).reason is TurnRelayFailure.UNREACHABLE

    def test_an_origin_that_closes_mid_body_is_a_failure(self, origin):
        url = origin.serve("/cut", http_response("200 OK", b"short", declared_length=100))
        assert failure_of(lambda: post(url, "token")).reason in set(TurnRelayFailure)


def log_unrelated_records(nonce: str) -> None:
    logging.getLogger("urllib3.connectionpool").debug("unrelated %s", nonce)
    try:
        raise ValueError(nonce)
    except ValueError:
        logging.getLogger("urllib3.connection").warning("unrelated %s", nonce, exc_info=True)


def emit_unrelated_records(nonce: str) -> None:
    thread = threading.Thread(target=log_unrelated_records, args=(nonce,))
    thread.start()
    thread.join()


class HeldCall(threading.Thread):
    def __init__(self, origin: Origin, answer: bytes, *, held: bool = True, nonce: str | None = None) -> None:
        super().__init__()
        self.nonce = nonce
        self.key_id, self.path = unique_key_path()
        self.token = secrets.token_urlsafe(32)
        self.url = origin.serve(self.path, answer, held=held)
        self.origin = origin
        self.error: TurnRelayUnavailableError | None = None
        self.start()
        assert origin.received[self.path].wait(ARRIVAL_CAP_SECONDS)

    def run(self) -> None:
        try:
            post(self.url, self.token)
        except TurnRelayUnavailableError as error:
            self.error = error
        if self.nonce is not None:
            log_unrelated_records(self.nonce)

    def finish(self) -> None:
        self.origin.release[self.path].set()
        self.join()


def assert_only_unrelated_records_arrived(records: list[str], nonces: list[str], calls: list[HeldCall]) -> None:
    for nonce in nonces:
        assert sum(f"unrelated {nonce}" in line for line in records) == 2
        assert sum(f"ValueError: {nonce}" in line for line in records) == 1
    secrets_of_calls = [value for call in calls for value in (call.key_id, call.token)]
    assert not any(value in line for line in records for value in secrets_of_calls)


class TestSilenceHoldsOnlyInsideEachCall:
    def test_a_malformed_header_line_logs_nothing_at_any_level_from_any_urllib3_logger(self, origin, urllib3_records):
        key_id, path = unique_key_path()
        sentinel = secrets.token_hex(8)
        url = origin.serve(path, http_response("200 OK", b"{}", extra_header=malformed_header_line(sentinel)))

        post(url, "token")

        assert not any(value in line for line in urllib3_records for value in [key_id, sentinel])

    def test_records_of_another_thread_arrive_when_the_calls_finish_nested(self, origin, urllib3_records):
        first = HeldCall(origin, answer_with_a_broken_header())
        second = HeldCall(origin, answer_with_a_broken_header())
        first_nonce, second_nonce = secrets.token_hex(8), secrets.token_hex(8)

        emit_unrelated_records(first_nonce)
        second.finish()
        first.finish()
        emit_unrelated_records(second_nonce)

        assert_only_unrelated_records_arrived(urllib3_records, [first_nonce, second_nonce], [first, second])

    def test_records_of_another_thread_arrive_when_the_calls_finish_crossed(self, origin, urllib3_records):
        first = HeldCall(origin, answer_with_a_broken_header())
        second = HeldCall(origin, answer_with_a_broken_header())
        nonces = [secrets.token_hex(8) for _ in range(2)]

        first.finish()
        emit_unrelated_records(nonces[0])
        second.finish()
        emit_unrelated_records(nonces[1])

        assert_only_unrelated_records_arrived(urllib3_records, nonces, [first, second])

    def test_records_of_another_thread_arrive_when_the_calls_fail(self, origin, urllib3_records):
        sentinel = secrets.token_hex(8)
        nonces = [secrets.token_hex(8) for _ in range(3)]
        first = HeldCall(
            origin,
            http_response("200 OK", b"short", extra_header=malformed_header_line(sentinel), declared_length=100),
            nonce=nonces[0],
        )
        second = HeldCall(origin, http_response("200 OK", b"short", declared_length=100), nonce=nonces[1])

        first.finish()
        emit_unrelated_records(nonces[2])
        second.finish()

        assert_only_unrelated_records_arrived(urllib3_records, nonces, [first, second])
        assert not any(sentinel in line for line in urllib3_records)
        assert all(call.error is not None for call in (first, second))
        assert all(
            value not in rendered_traceback(call.error)
            for call in (first, second)
            for value in (call.key_id, call.token)
        )

    def test_no_urllib3_logger_is_changed_by_the_calls(self, origin, urllib3_records):
        def snapshot():
            names = [name for name in logging.root.manager.loggerDict if name.startswith("urllib3")]
            return {
                name: (logger.level, list(logger.handlers), list(logger.filters), logger.disabled)
                for name, logger in ((name, logging.getLogger(name)) for name in names)
            }

        before = snapshot()
        call = HeldCall(origin, http_response("200 OK", b"{}"), held=False)
        call.join()
        logging.getLogger("urllib3.connectionpool").debug("from the main thread")

        assert snapshot() == before
        assert "from the main thread" in "".join(urllib3_records)
