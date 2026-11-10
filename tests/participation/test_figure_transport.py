"""The urllib3 adapter of the figure transport against a loopback origin: one streamed GET, no redirect followed."""

import secrets
import socket
import time

import pytest

from hopin.participation.figure_transport import get_figure_over_https
from hopin.participation.figures import FigureFetchError
from hopin.participation.vocabulary import FigureRefusal
from tests.support.loopback_origin import ARRIVAL_CAP_SECONDS, LOCALHOST, Origin, http_response

pytestmark = pytest.mark.integration

BODY = b"glTF" + bytes(64)
TIME_LEFT_SECONDS = 0.3
DRIP_SECONDS = 0.02
# Generous beside the time left, so a loaded machine does not flake, and far below a drip's full length.
RETURN_BOUND_SECONDS = 3
DRIPPED = bytes(1000)


@pytest.fixture
def origin():
    loopback = Origin()
    yield loopback
    loopback.close()


def unique_path():
    return f"/personas/{secrets.token_hex(8)}/mozart.glb"


def answer_of(url, timeout_seconds=ARRIVAL_CAP_SECONDS):
    with get_figure_over_https(url, timeout_seconds) as answer:
        return answer.status, answer.location, answer.content_length, b"".join(answer.body)


def failure_of(url, timeout_seconds=ARRIVAL_CAP_SECONDS):
    with pytest.raises(FigureFetchError) as failure:
        answer_of(url, timeout_seconds)
    return failure.value.refusal


class TestOneGet:
    def test_the_answer_streams_back_with_its_status_and_length_and_logs_no_path(self, origin, urllib3_records):
        path = unique_path()
        url = origin.serve(path, http_response("200 OK", BODY))

        assert answer_of(url) == (200, None, len(BODY), BODY)
        assert not any(path in line for line in urllib3_records)

    def test_the_request_carries_no_cookie_or_authorization_and_exactly_the_path(self, origin):
        path = unique_path()
        answer_of(origin.serve(path, http_response("200 OK", BODY)))

        [(method, seen_path, headers, _)] = origin.requests
        assert (method, seen_path) == ("GET", path)
        assert {name.lower() for name in headers} & {"cookie", "authorization"} == set()

    def test_a_redirect_comes_back_with_its_location_and_is_not_followed(self, origin):
        path = unique_path()
        url = origin.serve(path, http_response("302 Found", b"", extra_header=b"Location: /second\r\n"))
        origin.serve("/second", http_response("200 OK", BODY))

        assert (answer_of(url), origin.paths_requested()) == ((302, "/second", 0, b""), [path])

    def test_a_body_sent_without_a_length_comes_back_without_one(self, origin):
        head = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n"
        url = origin.serve(unique_path(), head + BODY)

        assert answer_of(url) == (200, None, None, BODY)

    def test_nothing_listening_is_unreachable(self):
        with socket.socket() as unused:
            unused.bind((LOCALHOST, 0))
            port = unused.getsockname()[1]

        assert failure_of(f"http://{LOCALHOST}:{port}/mozart.glb") is FigureRefusal.UNREACHABLE

    def test_an_address_urllib3_cannot_parse_is_malformed(self):
        assert failure_of(f"http://{LOCALHOST} x/mozart.glb") is FigureRefusal.MALFORMED_URL

    def test_an_origin_that_does_not_answer_within_the_time_left_is_past_the_deadline(self, origin):
        url = origin.serve(unique_path(), http_response("200 OK", BODY), held=True)

        assert failure_of(url, timeout_seconds=0.3) is FigureRefusal.PAST_DEADLINE

    def test_a_body_that_breaks_off_is_unreachable(self, origin):
        url = origin.serve(unique_path(), http_response("200 OK", b"short", declared_length=100))

        assert failure_of(url) is FigureRefusal.UNREACHABLE


class TestTheDeadline:
    @pytest.mark.parametrize(
        ("answer", "dripped"),
        [
            pytest.param(b"HTTP/1.1 200 OK\r\n", b"X-Padding: " + b"a" * len(DRIPPED), id="headers"),
            pytest.param(http_response("200 OK", b"", declared_length=len(DRIPPED)), DRIPPED, id="body with a length"),
            pytest.param(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n", DRIPPED, id="body without a length"),
        ],
    )
    def test_an_origin_that_drips_its_answer_is_ended_at_the_time_left(self, origin, answer, dripped):
        url = origin.serve(unique_path(), answer, dripped=dripped, every=DRIP_SECONDS)
        started = time.monotonic()

        refusal = failure_of(url, timeout_seconds=TIME_LEFT_SECONDS)

        assert (refusal, time.monotonic() - started < RETURN_BOUND_SECONDS) == (FigureRefusal.PAST_DEADLINE, True)
