"""An agent's figure at HopIn's real `/mcp`, `/ws` and figure route: the allowlist at accept, the path every seat is
told, and the route that serves the verified file while a joined agent names it."""

import pytest
import structlog.testing
from agents.member import ACCEPT_INVITATION_TOOL, LEASE_SECONDS

from hopin.agent.tools import LEAVE_CALL
from hopin.participation.figures import figure_refusal
from hopin.participation.settings import ParticipationSettings
from hopin.participation.vocabulary import FIGURE_PATH, AgentRefusal, FigureRefusal, ParticipationSignal
from tests.participation.support.figures import (
    SOURCE_PREFIX,
    Answer,
    FigureOrigin,
    digest_of,
    figure_url,
    glb,
    ok,
)
from tests.participation.support.own_accounts import ADDERS
from tests.support.dialed_call import accept_arguments
from tests.support.signed_in import cookie_header

pytestmark = pytest.mark.integration

JOINED = ParticipationSignal.AGENT_JOINED.value
OFFERS = ParticipationSignal.AGENT_OFFERS.value
SECOND_OWNER = "subject-second-owner"
FILE = glb(b"mozart" * 8)
REFUSED_EVENT = "agent_figure_refused"


@pytest.fixture
def figure_origin():
    return FigureOrigin()


@pytest.fixture
def sources():
    return [SOURCE_PREFIX]


@pytest.fixture
def figure_sources(sources):
    return ParticipationSettings(figure_sources=sources).figure_sources


@pytest.fixture
def figure_transport(figure_origin):
    return figure_origin


@pytest.fixture
def adding_call_adders(adding_call_adders):
    return adding_call_adders | {SECOND_OWNER: ADDERS}


def figure_of(url, body=FILE):
    return {"url": url, "sha256": digest_of(body)}


def accept_with(scene, figure, *, token=None, name=None):
    """The member adds the agent and it accepts with the figure, or with none."""
    if name is None:
        scene.invite()
    else:
        scene.add(key_offered(scene, name))
        scene.member.frame_of_type(OFFERS)
    token = token or scene.token
    standing = next(item for item in scene.call.standing(token) if item["state"] == "invited")
    arguments = accept_arguments(standing) | ({} if figure is None else {"figure": figure})
    return scene.call.call(token, ACCEPT_INVITATION_TOOL, arguments)


def key_offered(scene, name):
    """The key of the agent of that name in the first offers the member is sent that hold it."""
    while True:
        offers = scene.member.frame_of_type(OFFERS)["offers"]
        keys = [offer["agent_key"] for offer in offers if offer["name"] == name]
        if keys:
            return keys[0]


def read_figure(scene, path, method="GET", **headers):
    cookie = {"cookie": cookie_header(scene.member_login.set_cookie_headers)}
    return scene.call.client.request(method, path, headers=cookie | headers)


def joined_with(scene, url, body=FILE):
    """The agent accepted with the figure at the URL; the path the owner's seat was told."""
    assert not accept_with(scene, figure_of(url, body)).is_error
    return scene.owner.frame_of_type(JOINED)["figure"]


def refusals_in(logs):
    return [entry for entry in logs if entry["event"] == REFUSED_EVENT]


def is_active(scene):
    return [item["state"] for item in scene.standings()] == ["active"]


class TestTheFigureAtAccept:
    def test_an_accept_without_a_figure_joins_with_no_figure_on_the_tile(self, scene):
        accept_with(scene, None)

        assert "figure" not in scene.owner.frame_of_type(JOINED)

    def test_an_allowed_figure_is_told_to_every_seat_and_to_a_later_one_as_the_path_that_serves_it(
        self, scene, figure_origin
    ):
        figure_origin.serve(figure_url(), ok(FILE))
        accept_with(scene, figure_of(figure_url()))
        told = [seat.frame_of_type(JOINED).get("figure") for seat in (scene.owner, scene.member)]
        _, later_login = scene.call.member()
        later = scene.call.seat(later_login, scene.owner.room_id)

        assert told == [later.frame_of_type(JOINED)["figure"]] * 2
        assert read_figure(scene, told[0]).content == FILE

    @pytest.mark.parametrize("sources", [[]])
    def test_with_no_source_allowed_the_agent_joins_with_no_figure_and_the_code_is_logged(self, scene):
        with structlog.testing.capture_logs() as logs:
            accept_with(scene, figure_of(figure_url()))

        assert "figure" not in scene.owner.frame_of_type(JOINED)
        assert [entry["reason"] for entry in refusals_in(logs)] == [FigureRefusal.NO_SOURCE.value]

    @pytest.mark.parametrize(
        ("url", "code"),
        [
            pytest.param(figure_url().replace("figures.", "other."), FigureRefusal.OTHER_HOST, id="another host"),
            pytest.param(figure_url().replace(".test/", ".test:443/"), FigureRefusal.PORT, id="an explicit port"),
            pytest.param(figure_url().replace("figures.", "FIGURES."), FigureRefusal.UPPERCASE_HOST, id="uppercase"),
            pytest.param(f"{SOURCE_PREFIX}v0.1.0/%2e%2e/mozart.glb", FigureRefusal.PERCENT, id="an encoded dot-dot"),
            pytest.param(f"{SOURCE_PREFIX}./mozart.glb", FigureRefusal.DOT_SEGMENT, id="a dot segment"),
            pytest.param(f"{SOURCE_PREFIX}../../x/mozart.glb", FigureRefusal.DOT_DOT_SEGMENT, id="a dot-dot segment"),
            pytest.param(f"{SOURCE_PREFIX}v0.1.0//mozart.glb", FigureRefusal.EMPTY_SEGMENT, id="an empty segment"),
            pytest.param(f"{SOURCE_PREFIX}v0.1.0/", FigureRefusal.EMPTY_SEGMENT, id="no file name"),
            pytest.param(f"{SOURCE_PREFIX}moz+art.glb", FigureRefusal.DISALLOWED_CHARACTER, id="a plus"),
            pytest.param(f"{SOURCE_PREFIX}mözart.glb", FigureRefusal.NON_ASCII, id="a non-ASCII letter"),
            pytest.param(
                SOURCE_PREFIX.removesuffix("/") + "-lookalike/mozart.glb",
                FigureRefusal.OUTSIDE_PREFIX,
                id="a lookalike prefix",
            ),
            pytest.param("https://figures.example.test", FigureRefusal.OUTSIDE_PREFIX, id="no path"),
        ],
    )
    def test_a_figure_outside_the_allowed_sources_joins_with_no_figure_and_logs_only_its_code(
        self, scene, figure_origin, url, code
    ):
        with structlog.testing.capture_logs() as logs:
            accepted = accept_with(scene, figure_of(url))

        assert accepted.outcome == {"room": scene.owner.room_id, "name": scene.agent_name}
        assert "figure" not in scene.owner.frame_of_type(JOINED)
        assert refusals_in(logs) == [
            {"event": REFUSED_EVENT, "room_id": scene.owner.room_id, "reason": code.value, "log_level": "info"}
        ]
        assert figure_origin.requested == []

    @pytest.mark.parametrize(
        ("url", "code"),
        [
            pytest.param(figure_url().replace("https:", "http:"), FigureRefusal.NOT_HTTPS, id="http"),
            pytest.param(figure_url().replace("https:", "ftp:"), FigureRefusal.NOT_HTTPS, id="ftp"),
            pytest.param(figure_url().replace("https:", ""), FigureRefusal.NOT_HTTPS, id="no scheme"),
            pytest.param(f"{figure_url()}?v=1", FigureRefusal.QUERY, id="a query"),
            pytest.param(f"{figure_url()}#top", FigureRefusal.FRAGMENT, id="a fragment"),
            pytest.param(f"{figure_url()}?", FigureRefusal.ALTERED_BY_PARSING, id="an empty query"),
            pytest.param(figure_url().replace("https:", "https:\t"), FigureRefusal.ALTERED_BY_PARSING, id="a tab"),
            pytest.param(f" {figure_url()}", FigureRefusal.ALTERED_BY_PARSING, id="a leading space"),
            pytest.param(f"{figure_url()}\n", FigureRefusal.ALTERED_BY_PARSING, id="a trailing newline"),
        ],
    )
    def test_the_allowlist_check_alone_refuses_a_url_the_accept_record_refuses_before_it(
        self, figure_sources, url, code
    ):
        assert figure_refusal(figure_sources, url) is code

    @pytest.mark.parametrize(
        "figure",
        [
            pytest.param(figure_of(figure_url().replace("https:", "http:")), id="http"),
            pytest.param(figure_of(f"{figure_url()}?v=1"), id="a query"),
            pytest.param({"url": figure_url(), "sha256": digest_of(FILE).upper()}, id="an uppercase digest"),
        ],
    )
    def test_a_figure_the_accept_record_refuses_refuses_the_accept_as_a_malformed_argument(self, scene, figure):
        accepted = accept_with(scene, figure)

        assert (accepted.is_error, accepted.outcome) == (True, {"refused": AgentRefusal.STALE_INVITATION.value})
        assert [item["state"] for item in scene.standings()] == ["invited"]


class TestTheRoute:
    def test_a_verified_file_is_served_as_a_gltf_binary_that_is_kept_privately_for_good(self, scene, figure_origin):
        path = joined_with(scene, figure_origin.serve(figure_url(), ok(FILE)))

        served = read_figure(scene, path)

        assert (served.status_code, served.content) == (200, FILE)
        assert (
            served.headers["content-type"],
            served.headers["x-content-type-options"],
            served.headers["cache-control"],
        ) == ("model/gltf-binary", "nosniff", "private, max-age=31536000, immutable")

    def test_a_head_answers_the_headers_without_the_body(self, scene, figure_origin):
        path = joined_with(scene, figure_origin.serve(figure_url(), ok(FILE)))

        served = read_figure(scene, path, "HEAD")

        assert (served.status_code, served.content, served.headers["content-type"]) == (200, b"", "model/gltf-binary")

    @pytest.mark.parametrize(
        "digest",
        [
            pytest.param(digest_of(b"a file no agent names"), id="a digest no agent names"),
            pytest.param("not-a-digest", id="a malformed digest"),
        ],
    )
    def test_a_digest_no_joined_agent_names_is_not_found_and_not_kept(self, scene, figure_origin, digest):
        joined_with(scene, figure_origin.serve(figure_url(), ok(FILE)))

        served = read_figure(scene, f"{FIGURE_PATH}/{digest}")

        assert served.status_code == 404
        assert "immutable" not in served.headers.get("cache-control", "")
        assert figure_origin.requested == []

    def test_a_read_without_a_session_is_refused_by_the_gate(self, scene, figure_origin):
        path = joined_with(scene, figure_origin.serve(figure_url(), ok(FILE)))

        served = scene.call.client.get(path)

        assert served.status_code == 401
        assert figure_origin.requested == []

    def test_a_read_with_a_bearer_is_refused_by_the_gate(self, scene, figure_origin):
        path = joined_with(scene, figure_origin.serve(figure_url(), ok(FILE)))

        served = scene.call.client.get(path, headers={"authorization": f"Bearer {scene.token}"})

        assert served.status_code == 401
        assert figure_origin.requested == []

    @pytest.mark.parametrize(
        "end",
        [
            pytest.param(
                lambda scene, _: scene.call.call(scene.token, LEAVE_CALL, {"room": scene.owner.room_id}), id="leave"
            ),
            pytest.param(
                lambda scene, agent_id: scene.owner.send(
                    {"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent_id}
                ),
                id="removal",
            ),
            pytest.param(lambda scene, _: scene.call.pass_time(LEASE_SECONDS), id="the lease's end"),
        ],
    )
    def test_once_the_agent_has_gone_its_figure_is_not_found_though_the_cache_holds_it(self, scene, figure_origin, end):
        path = joined_with(scene, figure_origin.serve(figure_url(), ok(FILE)))
        agent_id = scene.member.frame_of_type(JOINED)["agent_id"]
        assert read_figure(scene, path).status_code == 200

        end(scene, agent_id)
        scene.member.frame_of_type(ParticipationSignal.AGENT_LEFT.value)

        assert read_figure(scene, path).status_code == 404

    def test_a_failed_fetch_answers_a_bad_gateway_and_the_agent_stays_joined(self, scene, figure_origin):
        path = joined_with(scene, figure_origin.serve(figure_url(), Answer(500)))

        served = read_figure(scene, path)

        assert served.status_code == 502
        assert "immutable" not in served.headers.get("cache-control", "")
        assert is_active(scene)

    def test_a_failed_fetch_is_tried_again_on_the_next_read(self, scene, figure_origin):
        path = joined_with(scene, figure_origin.serve(figure_url(), Answer(500)))
        read_figure(scene, path)
        figure_origin.serve(figure_url(), ok(FILE))

        assert (read_figure(scene, path).content, figure_origin.requested) == (FILE, [figure_url()] * 2)

    @pytest.mark.parametrize(
        "failing_first", [pytest.param(True, id="failing first"), pytest.param(False, id="good first")]
    )
    def test_two_agents_naming_one_digest_under_different_urls_one_failing_the_other_is_served(
        self, scene, figure_origin, failing_first
    ):
        failing = figure_origin.serve(figure_url("failing.glb"), ok(glb(b"another file")))
        good = figure_origin.serve(figure_url("good.glb"), ok(FILE))
        first, second_url = (failing, good) if failing_first else (good, failing)
        path = joined_with(scene, first)
        _, second_owner_login = scene.call.member(subject=SECOND_OWNER, groups=(ADDERS,))
        scene.call.seat(second_owner_login, scene.owner.room_id)
        second = scene.call.agent_token(owner=SECOND_OWNER, subject="second-agent", agent_name="Haydn")
        scene.call.standing(second)
        assert not accept_with(scene, figure_of(second_url), token=second, name="Haydn").is_error

        assert read_figure(scene, path).content == FILE
