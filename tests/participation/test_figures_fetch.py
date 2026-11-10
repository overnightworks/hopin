"""One figure's fetch from its source over the transport port: the redirect, the deadline, the bounds and the checks."""

import pytest

from hopin.participation.figures import Figure, fetch_figure
from hopin.participation.vocabulary import FIGURE_FETCH_SECONDS, FIGURE_MAX_BYTES, FigureRefusal
from tests.participation.support.figures import (
    GLB_HEADER,
    Answer,
    FakeClock,
    FigureOrigin,
    digest_of,
    figure_url,
    glb,
    ok,
    redirect,
)

FILE = glb()
MIRROR = "https://mirror.example.test/figures/mozart.glb"


@pytest.fixture
def origin():
    return FigureOrigin()


@pytest.fixture
def clock():
    return FakeClock()


def fetched(origin, clock, url=None, sha256=None):
    return fetch_figure(
        origin, Figure(url or figure_url(), digest_of(FILE) if sha256 is None else sha256), monotonic=clock
    )


class TestAFetchThatVerifies:
    def test_the_file_comes_back_and_the_source_was_asked_exactly_the_checked_url(self, origin, clock):
        origin.serve(figure_url(), ok(FILE))

        assert (fetched(origin, clock), origin.requested) == (FILE, [figure_url()])

    def test_the_whole_fetch_is_given_the_deadline(self, origin, clock):
        origin.serve(figure_url(), ok(FILE))

        fetched(origin, clock)

        assert origin.timeouts == [FIGURE_FETCH_SECONDS]

    @pytest.mark.parametrize(
        "location",
        [pytest.param(MIRROR, id="an absolute https address"), pytest.param("/elsewhere/mozart.glb", id="a path")],
    )
    def test_one_https_redirect_is_followed_and_served(self, origin, clock, location):
        origin.serve(figure_url(), redirect(location))
        target = MIRROR if location == MIRROR else "https://figures.example.test/elsewhere/mozart.glb"
        origin.serve(target, ok(FILE))

        assert (fetched(origin, clock), origin.requested) == (FILE, [figure_url(), target])


def wrong_magic():
    return b"glTX" + FILE[4:]


def wrong_version():
    return GLB_HEADER.pack(b"glTF", 1, len(FILE)) + FILE[GLB_HEADER.size :]


def wrong_length():
    return GLB_HEADER.pack(b"glTF", 2, len(FILE) + 4) + FILE[GLB_HEADER.size :]


class TestAFetchThatIsRefused:
    def test_bytes_whose_digest_differs_are_a_mismatch(self, origin, clock):
        origin.serve(figure_url(), ok(FILE))

        assert fetched(origin, clock, sha256=digest_of(b"another file")) is FigureRefusal.MISMATCH

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(wrong_magic(), id="another magic"),
            pytest.param(wrong_version(), id="glTF 1"),
            pytest.param(wrong_length(), id="a length that is not the file's"),
            pytest.param(b"glTF", id="shorter than the header"),
        ],
    )
    def test_a_file_without_the_gltf_binary_header_is_a_bad_header_even_when_its_digest_matches(
        self, origin, clock, body
    ):
        origin.serve(figure_url(), ok(body))

        assert fetched(origin, clock, sha256=digest_of(body)) is FigureRefusal.BAD_HEADER

    def test_a_body_announced_over_the_bound_is_cut_before_its_first_chunk(self, origin, clock):
        chunks_read = []
        origin.serve(
            figure_url(),
            Answer(200, FILE, content_length=FIGURE_MAX_BYTES + 1, on_chunk=lambda: chunks_read.append(1)),
        )

        assert (fetched(origin, clock), chunks_read) == (FigureRefusal.TOO_LARGE, [])

    def test_a_body_streamed_over_the_bound_without_a_length_is_cut_at_the_bound(self, origin, clock):
        chunks_read = []
        body = bytes(FIGURE_MAX_BYTES + 1) + bytes(FIGURE_MAX_BYTES)
        chunk_bytes = FIGURE_MAX_BYTES // 4
        origin.serve(figure_url(), Answer(200, body, chunk_bytes=chunk_bytes, on_chunk=lambda: chunks_read.append(1)))

        assert fetched(origin, clock) is FigureRefusal.TOO_LARGE
        assert len(chunks_read) * chunk_bytes <= FIGURE_MAX_BYTES + chunk_bytes

    def test_a_stream_still_running_at_the_deadline_is_cut(self, origin, clock):
        chunks_read = []

        def slow_chunk():
            chunks_read.append(1)
            clock.now += FIGURE_FETCH_SECONDS / 2

        origin.serve(figure_url(), Answer(200, FILE, chunk_bytes=1, on_chunk=slow_chunk))

        assert fetched(origin, clock) is FigureRefusal.PAST_DEADLINE
        assert len(chunks_read) < len(FILE)

    def test_the_deadline_spans_the_redirect_so_a_slow_first_answer_leaves_no_time_for_the_second(self, origin, clock):
        def slow_answer():
            clock.now += FIGURE_FETCH_SECONDS

        origin.serve(figure_url(), Answer(302, location=MIRROR, on_get=slow_answer))
        origin.serve(MIRROR, ok(FILE))

        assert (fetched(origin, clock), origin.requested) == (FigureRefusal.PAST_DEADLINE, [figure_url()])

    def test_the_second_answer_is_given_only_the_time_left(self, origin, clock):
        def slow_answer():
            clock.now += FIGURE_FETCH_SECONDS / 3

        origin.serve(figure_url(), Answer(302, location=MIRROR, on_get=slow_answer))
        origin.serve(MIRROR, ok(FILE))

        fetched(origin, clock)

        assert origin.timeouts == [FIGURE_FETCH_SECONDS, FIGURE_FETCH_SECONDS * 2 / 3]

    def test_a_second_redirect_is_refused_and_not_followed(self, origin, clock):
        origin.serve(figure_url(), redirect(MIRROR))
        origin.serve(MIRROR, redirect(figure_url("third.glb")))
        origin.serve(figure_url("third.glb"), ok(FILE))

        assert (fetched(origin, clock), origin.requested) == (FigureRefusal.SECOND_REDIRECT, [figure_url(), MIRROR])

    @pytest.mark.parametrize(
        ("location", "refusal"),
        [
            pytest.param("http://figures.example.test/mozart.glb", FigureRefusal.REDIRECT_NOT_HTTPS, id="to http"),
            pytest.param("https://[bad/mozart.glb", FigureRefusal.MALFORMED_URL, id="to a malformed address"),
        ],
    )
    def test_a_redirect_to_an_address_that_is_not_https_or_malformed_is_refused_with_its_code_and_not_followed(
        self, origin, clock, location, refusal
    ):
        origin.serve(figure_url(), redirect(location))

        assert (fetched(origin, clock), origin.requested) == (refusal, [figure_url()])

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(Answer(404), id="not found"),
            pytest.param(Answer(500), id="a server error"),
            pytest.param(Answer(204), id="no content"),
            pytest.param(Answer(302), id="a redirect without a location"),
        ],
    )
    def test_an_answer_that_is_not_200_is_not_ok(self, origin, clock, answer):
        origin.serve(figure_url(), answer)

        assert fetched(origin, clock) is FigureRefusal.NOT_OK

    @pytest.mark.parametrize("failure", [FigureRefusal.UNREACHABLE, FigureRefusal.PAST_DEADLINE])
    def test_a_transport_failure_is_refused_with_the_transports_code(self, origin, clock, failure):
        origin.serve(figure_url(), Answer(broken=failure))

        assert fetched(origin, clock) is failure

    def test_a_source_that_does_not_answer_is_unreachable(self, origin, clock):
        assert fetched(origin, clock) is FigureRefusal.UNREACHABLE
