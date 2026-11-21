"""An agent's figure at HopIn's real `/mcp`, `/ws` and figure route: the allowlist at accept, the path every seat is
told, and the route that serves the verified file while a joined agent names it."""

import pytest
import structlog.testing
from agents.member import (
    ACCEPT_INVITATION_TOOL,
    GESTURE_TOOL,
    LEASE_SECONDS,
    SET_FRAMING_TOOL,
    SET_TURN_STATE_TOOL,
    SPEAK_TOOL,
    STANDING_URI,
)

from hopin.agent.tools import LEAVE_CALL, SAY
from hopin.config.constants import CALL_FEATURES_PATH
from hopin.participation.figures import figure_refusal
from hopin.participation.settings import ParticipationSettings
from hopin.participation.vocabulary import (
    CLIP_MAX_BYTES,
    CLIP_PATH,
    FEATURE_NAME,
    FIGURE_MODULE,
    FIGURE_PATH,
    THINKING_MAX_SECONDS,
    FigureRefusal,
    ParticipationSignal,
)
from hopin.rooms.reference import room_reference
from tests.participation.support.figures import (
    SOURCE_PREFIX,
    Answer,
    FigureOrigin,
    digest_of,
    figure_url,
    glb,
    ok,
    redirect,
)
from tests.participation.support.tool_calls import call_in
from tests.support.agent_adders import ADDERS_GROUP
from tests.support.dialed_call import LISTEN_TIMEOUT_SECONDS, RESOURCE_UPDATED, TOOLS_LIST_CHANGED, accept_arguments
from tests.support.signed_in import cookie_header

pytestmark = pytest.mark.integration

JOINED = ParticipationSignal.AGENT_JOINED.value
OFFERS = ParticipationSignal.AGENT_OFFERS.value
GESTURED = ParticipationSignal.AGENT_GESTURE.value
FRAMED = ParticipationSignal.AGENT_FRAMING.value
THINKING = ParticipationSignal.AGENT_THINKING.value
TURN_STATE = ParticipationSignal.AGENT_TURN_STATE.value
SECOND_OWNER = "subject-second-owner"
FILE = glb(b"mozart" * 8)
CLIP = glb(b"wave" * 8)
REFUSED_EVENT = "agent_figure.refused"
CLIP_REFUSED_EVENT = "agent_clip.refused"
WAVE = "wave"


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
    return adding_call_adders | {SECOND_OWNER: ADDERS_GROUP}


def figure_of(url, body=FILE, clips=None):
    """The accept's figure; `clips` maps a gesture to the URL and the bytes of the clip that replaces it."""
    gestures = {name: {"url": clip_url, "sha256": digest_of(clip)} for name, (clip_url, clip) in (clips or {}).items()}
    return {"url": url, "sha256": digest_of(body)} | ({"gestures": gestures} if gestures else {})


def accept_with(scene, figure, *, token=None, name=None):
    """The member adds the agent and it accepts with the figure, or with none."""
    if name is None:
        scene.invite()
    else:
        scene.add(key_offered(scene, name))
        scene.member.frame_of_type(OFFERS)
    return accept_invitation(scene, figure, token=token)


def accept_invitation(scene, figure, *, token=None):
    """The agent accepts the invitation it holds, with the figure or with none."""
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


def refusals_in(logs, event=REFUSED_EVENT):
    return [entry for entry in logs if entry["event"] == event]


def clip_of_wave():
    return figure_url("wave.vrma")


def joined_with_clip(scene, figure_origin, clip=CLIP, answer=None):
    """The agent accepted with a figure and a clip for the wave, which the origin answers with `answer` (the clip's
    own bytes when none); the path the owner's seat was told for the clip."""
    figure_origin.serve(figure_url(), ok(FILE))
    figure_origin.serve(clip_of_wave(), ok(clip) if answer is None else answer)
    assert not accept_with(scene, figure_of(figure_url(), clips={WAVE: (clip_of_wave(), clip)})).is_error
    return scene.owner.frame_of_type(JOINED)["gestures"][WAVE]


def is_active(scene):
    return [item["state"] for item in scene.standings()] == ["active"]


class TestTheFigureModule:
    @pytest.mark.parametrize(
        ("sources", "modules"),
        [
            pytest.param([SOURCE_PREFIX], [FEATURE_NAME, FIGURE_MODULE], id="a source allowed"),
            pytest.param([], [FEATURE_NAME], id="no source allowed"),
        ],
    )
    def test_the_page_installs_the_figure_module_only_when_a_figure_source_is_allowed(self, scene, modules):
        """CALL-56: Only a call with an allowed figure source lists the figure module the page installs."""
        listed = read_figure(scene, CALL_FEATURES_PATH).json()["features"]

        assert [module for module in listed if module in {FEATURE_NAME, FIGURE_MODULE}] == modules


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
            {
                "event": REFUSED_EVENT,
                "room_ref": room_reference(scene.owner.room_id),
                "reason": code.value,
                "log_level": "info",
            }
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
            pytest.param("https://[allowed.example]/p/a.glb", FigureRefusal.MALFORMED_URL, id="a bracketed host"),
            pytest.param("https://allowed.example]/p/a.glb", FigureRefusal.MALFORMED_URL, id="a lone closing bracket"),
            pytest.param("https://allowed.example\uff0f/p/a.glb", FigureRefusal.MALFORMED_URL, id="a fullwidth slash"),
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
            pytest.param(
                figure_of(figure_url(), clips={WAVE: (f"{clip_of_wave()}?v=1", CLIP)}), id="a clip with a query"
            ),
            pytest.param({"url": figure_url(), "sha256": digest_of(FILE).upper()}, id="an uppercase digest"),
        ],
    )
    def test_a_figure_the_accept_record_refuses_is_a_tool_error_naming_the_figure(self, scene, figure):
        accepted = accept_with(scene, figure)

        assert (accepted.is_error, accepted.outcome) == (True, None)
        assert accepted.text.partition(": ")[2].startswith("figure")
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
        _, second_owner_login = scene.call.member(subject=SECOND_OWNER, groups=(ADDERS_GROUP,))
        scene.call.seat(second_owner_login, scene.owner.room_id)
        second = scene.call.agent_token(owner=SECOND_OWNER, subject="second-agent", agent_name="Haydn")
        scene.call.standing(second)
        scene.call.listen(second, [STANDING_URI])
        assert not accept_with(scene, figure_of(second_url), token=second, name="Haydn").is_error

        assert read_figure(scene, path).content == FILE


class TestThePersonasClips:
    def test_an_allowed_clip_is_told_to_every_seat_and_to_a_later_one_as_the_path_that_serves_it(
        self, scene, figure_origin
    ):
        figure_origin.serve(figure_url(), ok(FILE))
        figure_origin.serve(clip_of_wave(), ok(CLIP))
        accept_with(scene, figure_of(figure_url(), clips={WAVE: (clip_of_wave(), CLIP)}))
        told = [seat.frame_of_type(JOINED).get("gestures") for seat in (scene.owner, scene.member)]
        _, later_login = scene.call.member()
        later = scene.call.seat(later_login, scene.owner.room_id)

        assert told == [later.frame_of_type(JOINED)["gestures"]] * 2
        path = told[0][WAVE]
        assert (path.startswith(f"{CLIP_PATH}/"), read_figure(scene, path).content) == (True, CLIP)

    def test_an_accept_without_a_clip_joins_with_no_gestures_on_the_tile(self, scene, figure_origin):
        figure_origin.serve(figure_url(), ok(FILE))
        accept_with(scene, figure_of(figure_url()))

        assert "gestures" not in scene.owner.frame_of_type(JOINED)

    def test_a_clip_is_served_as_a_gltf_binary_that_is_kept_privately_for_good(self, scene, figure_origin):
        path = joined_with_clip(scene, figure_origin)

        served = read_figure(scene, path)

        assert (served.status_code, served.content) == (200, CLIP)
        assert (
            served.headers["content-type"],
            served.headers["x-content-type-options"],
            served.headers["cache-control"],
        ) == ("model/gltf-binary", "nosniff", "private, max-age=31536000, immutable")

    def test_a_clip_is_fetched_once_for_every_read(self, scene, figure_origin):
        path = joined_with_clip(scene, figure_origin)

        reads = [read_figure(scene, path).content for _ in range(2)]

        assert (reads, figure_origin.requested.count(clip_of_wave())) == ([CLIP] * 2, 1)

    @pytest.mark.parametrize(
        ("url", "code"),
        [
            pytest.param(figure_url("wave.vrma").replace("figures.", "other."), FigureRefusal.OTHER_HOST, id="host"),
            pytest.param(f"{SOURCE_PREFIX}../wave.vrma", FigureRefusal.DOT_DOT_SEGMENT, id="a dot-dot segment"),
        ],
    )
    def test_a_clip_outside_the_allowed_sources_is_dropped_the_figure_stays_and_only_its_code_is_logged(
        self, scene, figure_origin, url, code
    ):
        figure_origin.serve(figure_url(), ok(FILE))
        with structlog.testing.capture_logs() as logs:
            accept_with(scene, figure_of(figure_url(), clips={WAVE: (url, CLIP)}))

        joined = scene.owner.frame_of_type(JOINED)
        assert ("figure" in joined, "gestures" in joined) == (True, False)
        assert refusals_in(logs, CLIP_REFUSED_EVENT) == [
            {
                "event": CLIP_REFUSED_EVENT,
                "room_ref": room_reference(scene.owner.room_id),
                "gesture": WAVE,
                "reason": code.value,
                "log_level": "info",
            }
        ]
        assert figure_origin.requested == []

    def test_a_clip_of_an_agent_whose_figure_is_refused_is_dropped_with_it(self, scene, figure_origin):
        other_host = figure_url().replace("figures.", "other.")
        accept_with(scene, figure_of(other_host, clips={WAVE: (clip_of_wave(), CLIP)}))

        joined = scene.owner.frame_of_type(JOINED)

        assert ("figure" in joined, "gestures" in joined) == (False, False)

    def test_a_clip_that_redirects_outside_the_allowed_sources_is_a_bad_gateway_and_the_other_host_is_not_asked(
        self, scene, figure_origin
    ):
        elsewhere = figure_url("wave.vrma").replace("figures.", "other.")

        clip_path = joined_with_clip(scene, figure_origin, answer=redirect(elsewhere))
        served = read_figure(scene, clip_path)

        assert (served.status_code, figure_origin.requested.count(elsewhere)) == (502, 0)

    @pytest.mark.parametrize(
        ("clip", "answer"),
        [
            pytest.param(CLIP, Answer(404), id="not found"),
            pytest.param(CLIP, ok(glb(b"another clip")), id="a wrong digest"),
            pytest.param(glb(b"\x00" * CLIP_MAX_BYTES), None, id="over the clip bound"),
            pytest.param(b"not a glb", None, id="no glTF header"),
        ],
    )
    def test_a_clip_that_cannot_be_verified_is_a_bad_gateway_and_the_figure_and_the_agent_stand(
        self, scene, figure_origin, clip, answer
    ):
        clip_path = joined_with_clip(scene, figure_origin, clip, answer)

        served = read_figure(scene, clip_path)

        assert served.status_code == 502
        assert "immutable" not in served.headers.get("cache-control", "")
        joined = next(frame for frame in scene.owner.received if frame["type"] == JOINED)
        assert read_figure(scene, joined["figure"]).content == FILE
        assert is_active(scene)

    def test_the_clip_bound_is_not_the_figure_bound(self, scene, figure_origin):
        figure = glb(b"\x00" * CLIP_MAX_BYTES)
        figure_origin.serve(figure_url(), ok(figure))

        assert read_figure(scene, joined_with(scene, figure_url(), figure)).content == figure

    @pytest.mark.parametrize("on_clip_route", [True, False])
    def test_a_digest_is_read_only_at_the_route_of_its_own_kind(self, scene, figure_origin, on_clip_route):
        joined_with_clip(scene, figure_origin)
        digest, route = (digest_of(FILE), CLIP_PATH) if on_clip_route else (digest_of(CLIP), FIGURE_PATH)

        served = read_figure(scene, f"{route}/{digest}")

        assert served.status_code == 404

    def test_once_the_agent_has_gone_its_clip_is_not_found_though_the_cache_holds_it(self, scene, figure_origin):
        path = joined_with_clip(scene, figure_origin)
        assert read_figure(scene, path).status_code == 200

        scene.call.call(scene.token, LEAVE_CALL, {"room": scene.owner.room_id})
        scene.member.frame_of_type(ParticipationSignal.AGENT_LEFT.value)

        assert read_figure(scene, path).status_code == 404

    def test_a_read_of_a_clip_without_a_session_is_refused_by_the_gate(self, scene, figure_origin):
        path = joined_with_clip(scene, figure_origin)

        assert scene.call.client.get(path).status_code == 401


def offered_tools(scene):
    answer = scene.call.request(scene.token, "tools/list").json()
    return {tool["name"] for tool in answer["result"]["tools"]}


@pytest.fixture
def gesturing(scene, figure_origin):
    """The scene with the agent joined with a shown figure."""
    joined_with(scene, figure_origin.serve(figure_url(), ok(FILE)))
    scene.agent_id = scene.member.frame_of_type(JOINED)["agent_id"]
    return scene


class TestTheGestureTool:
    def test_call_64_an_agent_with_a_shown_figure_is_offered_the_gesture_tool(self, gesturing):
        assert GESTURE_TOOL in offered_tools(gesturing)

    def test_call_64_an_agent_whose_figure_was_refused_is_not_offered_it(self, scene):
        accept_with(scene, figure_of(figure_url().replace("figures.", "other.")))

        assert GESTURE_TOOL not in offered_tools(scene)

    def test_call_64_an_agent_without_a_figure_is_not_offered_it(self, scene):
        accept_with(scene, None)

        assert GESTURE_TOOL not in offered_tools(scene)

    @pytest.mark.parametrize(
        ("joined_before", "change", "gained"),
        [
            pytest.param(
                False, lambda scene: accept_invitation(scene, figure_of(figure_url())), True, id="a figure shown"
            ),
            pytest.param(
                True,
                lambda scene: scene.call.call(scene.token, LEAVE_CALL, {"room": scene.owner.room_id}),
                False,
                id="the agent leaving",
            ),
        ],
    )
    def test_the_tools_notice_comes_before_the_wake_of_a_change_that_alters_the_gesture_tool(
        self, scene, figure_origin, joined_before, change, gained
    ):
        figure_origin.serve(figure_url(), ok(FILE))
        scene.invite()
        if joined_before:
            accept_invitation(scene, figure_of(figure_url()))
        before = GESTURE_TOOL in offered_tools(scene)
        with scene.call.open_listen(scene.token, [STANDING_URI], tools=True) as stream:
            change(scene)
            assert stream.wait_for(RESOURCE_UPDATED, LISTEN_TIMEOUT_SECONDS)

            assert (before, GESTURE_TOOL in offered_tools(scene)) == (not gained, gained)
            assert stream.arrivals(TOOLS_LIST_CHANGED, RESOURCE_UPDATED) == [TOOLS_LIST_CHANGED, RESOURCE_UPDATED]

    def test_call_64_a_gesture_is_relayed_and_every_seat_is_told_it_with_the_agent_s_id(self, gesturing):
        gestured = call_in(gesturing, tool=GESTURE_TOOL, gesture="wave")

        assert (gestured.is_error, gestured.outcome) == (False, {"relayed": True})
        for seat in (gesturing.owner, gesturing.member):
            told = seat.frame_of_type(GESTURED)
            assert (told["agent_id"], told["gesture"]) == (gesturing.agent_id, "wave")

    def test_call_64_a_seat_that_joins_later_is_told_no_past_gesture(self, gesturing):
        call_in(gesturing, tool=GESTURE_TOOL, gesture="wave")
        gesturing.owner.frame_of_type(GESTURED)
        _, later_login = gesturing.call.member()
        later = gesturing.call.seat(later_login, gesturing.owner.room_id)
        later.frame_of_type(JOINED)

        assert [frame for frame in later.received if frame["type"] == GESTURED] == []

    @pytest.mark.parametrize(
        "arguments",
        [
            pytest.param({"gesture": "wave", "place": {"kind": "call", "id": "no-such-room"}}, id="another room"),
            pytest.param({"gesture": "wave", "place": {"kind": "table", "id": "any"}}, id="a place of another kind"),
        ],
    )
    def test_call_64_a_gesture_at_no_call_the_agent_holds_is_not_active(self, gesturing, arguments):
        dialed = gesturing.call.call(gesturing.token, GESTURE_TOOL, arguments)

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": "not_active"})

    def test_call_64_the_gesture_tool_is_withdrawn_once_the_agent_left(self, gesturing):
        gesturing.call.call(gesturing.token, LEAVE_CALL, {"room": gesturing.owner.room_id})

        assert GESTURE_TOOL not in offered_tools(gesturing)

    def test_call_64_a_gesture_while_the_sponsors_seat_is_parked_is_not_active_and_tells_no_seat(
        self, gesturing, seat_changes
    ):
        seat_changes.clear()
        gesturing.owner.drop()
        assert seat_changes.wait(timeout=10)

        dialed = call_in(gesturing, tool=GESTURE_TOOL, gesture="wave")

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": "not_active"})
        assert GESTURED not in [frame["type"] for frame in gesturing.member.received]


class TestTheFramingTool:
    def test_call_77_an_agent_with_a_shown_figure_is_offered_the_framing_tool_until_it_leaves(self, gesturing):
        offered = SET_FRAMING_TOOL in offered_tools(gesturing)
        gesturing.call.call(gesturing.token, LEAVE_CALL, {"room": gesturing.owner.room_id})

        assert (offered, SET_FRAMING_TOOL in offered_tools(gesturing)) == (True, False)

    def test_call_77_an_agent_without_a_figure_is_not_offered_it(self, scene):
        accept_with(scene, None)

        assert SET_FRAMING_TOOL not in offered_tools(scene)

    @pytest.mark.parametrize("framing", ["face", "shoulders", "upperBody", "fullBody"])
    def test_call_77_a_framing_is_taken_and_every_seat_is_told_it_with_the_agent_s_id(self, gesturing, framing):
        framed = call_in(gesturing, tool=SET_FRAMING_TOOL, framing=framing)

        assert (framed.is_error, framed.outcome) == (False, {})
        for seat in (gesturing.owner, gesturing.member):
            told = seat.frame_of_type(FRAMED)
            assert (told["agent_id"], told["framing"]) == (gesturing.agent_id, framing)

    def test_call_77_a_framing_set_again_tells_no_seat_twice(self, gesturing):
        call_in(gesturing, tool=SET_FRAMING_TOOL, framing="face")
        gesturing.member.frame_of_type(FRAMED)

        again = call_in(gesturing, tool=SET_FRAMING_TOOL, framing="face")
        call_in(gesturing, tool=SET_FRAMING_TOOL, framing="fullBody")

        assert (again.is_error, again.outcome) == (False, {})
        assert gesturing.member.frame_of_type(FRAMED)["framing"] == "fullBody"

    def test_call_78_a_seat_that_joins_later_is_told_the_kept_framing_with_the_agent_s_tile(self, gesturing):
        call_in(gesturing, tool=SET_FRAMING_TOOL, framing="upperBody")
        _, later_login = gesturing.call.member()
        later = gesturing.call.seat(later_login, gesturing.owner.room_id)

        assert later.frame_of_type(JOINED)["framing"] == "upperBody"

    def test_call_78_the_framing_stands_through_a_pause_of_the_agent_s_place(self, gesturing, seat_changes):
        call_in(gesturing, tool=SET_FRAMING_TOOL, framing="face")
        seat_changes.clear()
        gesturing.owner.drop()
        assert seat_changes.wait(timeout=10)
        _, later_login = gesturing.call.member()
        later = gesturing.call.seat(later_login, gesturing.owner.room_id)

        assert later.frame_of_type(JOINED)["framing"] == "face"

    def test_call_78_a_tile_told_before_any_framing_carries_none(self, gesturing):
        _, later_login = gesturing.call.member()
        later = gesturing.call.seat(later_login, gesturing.owner.room_id)

        assert "framing" not in later.frame_of_type(JOINED)

    @pytest.mark.parametrize(
        "arguments",
        [
            pytest.param({"framing": "face", "place": {"kind": "call", "id": "no-such-room"}}, id="another room"),
            pytest.param({"framing": "face", "place": {"kind": "table", "id": "any"}}, id="a place of another kind"),
        ],
    )
    def test_call_77_a_framing_at_no_call_the_agent_holds_is_not_active(self, gesturing, arguments):
        dialed = gesturing.call.call(gesturing.token, SET_FRAMING_TOOL, arguments)

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": "not_active"})

    def test_call_77_a_framing_while_the_sponsors_seat_is_parked_is_not_active_and_tells_no_seat(
        self, gesturing, seat_changes
    ):
        seat_changes.clear()
        gesturing.owner.drop()
        assert seat_changes.wait(timeout=10)

        dialed = call_in(gesturing, tool=SET_FRAMING_TOOL, framing="face")

        assert (dialed.is_error, dialed.outcome) == (True, {"refused": "not_active"})
        assert FRAMED not in [frame["type"] for frame in gesturing.member.received]

    def test_call_77_a_framing_the_schema_does_not_name_fails_the_schema(self, gesturing):
        dialed = call_in(gesturing, tool=SET_FRAMING_TOOL, framing="closeup")

        assert dialed.is_error


def thinking_told(seat):
    """The next thinking frame the seat is told, as the agent's id and whether it thinks."""
    told = seat.frame_of_type(THINKING)
    return told["agent_id"], told["on"]


def outlast_the_cap(scene):
    """Let the thinking's cap run out while a call of the agent keeps its lease, which runs as long."""
    scene.call.pass_time(THINKING_MAX_SECONDS - 1)
    offered_tools(scene)
    scene.call.pass_time(1)


class TestThinking:
    @pytest.fixture
    def thinking(self, gesturing):
        """The scene with the agent stating that a turn of it runs, which every seat has been told."""
        stated = call_in(gesturing, tool=SET_TURN_STATE_TOOL, thinking=True)
        assert not stated.is_error
        for seat in (gesturing.owner, gesturing.member):
            assert thinking_told(seat) == (gesturing.agent_id, True)
        return gesturing

    def test_call_68_a_stated_thinking_is_told_to_every_seat_and_to_a_later_one(self, thinking):
        _, later_login = thinking.call.member()
        later = thinking.call.seat(later_login, thinking.owner.room_id)
        later.frame_of_type(JOINED)

        assert thinking_told(later) == (thinking.agent_id, True)

    @pytest.mark.parametrize(
        "ending",
        [
            pytest.param(lambda scene: call_in(scene, tool=SPEAK_TOOL, text="Hallo"), id="a speak"),
            pytest.param(lambda scene: call_in(scene, tool=GESTURE_TOOL, gesture="wave"), id="a gesture"),
            pytest.param(lambda scene: call_in(scene, tool=SET_TURN_STATE_TOOL), id="a statement of none"),
            pytest.param(lambda scene: call_in(scene, tool=SET_TURN_STATE_TOOL, stopped="busy"), id="a stop"),
            pytest.param(outlast_the_cap, id="the cap"),
        ],
    )
    def test_call_68_every_seat_is_told_the_thinking_ended(self, thinking, ending):
        ending(thinking)

        for seat in (thinking.owner, thinking.member):
            assert thinking_told(seat) == (thinking.agent_id, False)

    def test_call_68_a_repeated_thinking_runs_the_cap_afresh(self, thinking):
        thinking.call.pass_time(THINKING_MAX_SECONDS - 1)
        call_in(thinking, tool=SET_TURN_STATE_TOOL, thinking=True)
        thinking.call.pass_time(THINKING_MAX_SECONDS - 1)
        offered_tools(thinking)
        thinking.owner.settle()
        assert [frame["on"] for frame in thinking.owner.received if frame["type"] == THINKING] == [True]

        thinking.call.pass_time(1)

        assert thinking_told(thinking.owner) == (thinking.agent_id, False)

    @pytest.mark.parametrize(
        "place",
        [
            pytest.param(lambda room_id: {"kind": "call", "id": "no-such-room"}, id="another room"),
            pytest.param(lambda room_id: {"kind": "table", "id": room_id}, id="a place of another kind with its id"),
        ],
    )
    def test_call_68_a_statement_at_a_place_the_agent_does_not_hold_is_not_held(self, gesturing, place):
        arguments = {"place": place(gesturing.owner.room_id), "thinking": True}

        stated = gesturing.call.call(gesturing.token, SET_TURN_STATE_TOOL, arguments)

        assert (stated.is_error, stated.outcome) == (True, {"refused": "not_held"})

    def test_call_68_a_statement_while_the_sponsors_seat_is_parked_is_accepted_and_told(self, gesturing, seat_changes):
        seat_changes.clear()
        gesturing.owner.drop()
        assert seat_changes.wait(timeout=10)

        stated = call_in(gesturing, tool=SET_TURN_STATE_TOOL, thinking=True)

        assert not stated.is_error
        assert thinking_told(gesturing.member) == (gesturing.agent_id, True)

    def test_call_68_a_thinking_whose_agent_left_tells_no_seat_its_end_at_the_cap(self, thinking):
        thinking.call.call(thinking.token, LEAVE_CALL, {"room": thinking.owner.room_id})
        thinking.owner.frame_of_type(ParticipationSignal.AGENT_LEFT.value)

        outlast_the_cap(thinking)
        thinking.owner.settle()

        assert [frame["on"] for frame in thinking.owner.received if frame["type"] == THINKING] == [True]


def turn_state_told(seat):
    """The next turn state frame the seat is told, as the agent's id and the stated outcome, none once cleared."""
    told = seat.frame_of_type(TURN_STATE)
    return told["agent_id"], told["outcome"]


class TestStatedTurnOutcome:
    @pytest.fixture
    def stopped(self, gesturing):
        """The scene with the agent stating that its turns there stop as busy, which every seat has been told."""
        stated = call_in(gesturing, tool=SET_TURN_STATE_TOOL, stopped="busy")
        assert not stated.is_error
        for seat in (gesturing.owner, gesturing.member):
            assert turn_state_told(seat) == (gesturing.agent_id, "busy")
        return gesturing

    def test_call_73_a_stated_outcome_is_told_to_every_seat_and_a_later_one_gets_the_current(self, stopped):
        call_in(stopped, tool=SET_TURN_STATE_TOOL, stopped="budget_spent")
        _, later_login = stopped.call.member()
        later = stopped.call.seat(later_login, stopped.owner.room_id)
        later.frame_of_type(JOINED)

        assert turn_state_told(later) == (stopped.agent_id, "budget_spent")

    @pytest.mark.parametrize(
        "ending",
        [
            pytest.param(lambda scene: call_in(scene, tool=SET_TURN_STATE_TOOL, thinking=True), id="a new turn"),
            pytest.param(lambda scene: call_in(scene, tool=SET_TURN_STATE_TOOL), id="a statement of none"),
            pytest.param(lambda scene: call_in(scene, tool=SPEAK_TOOL, text="Hallo"), id="a spoken line"),
            pytest.param(lambda scene: call_in(scene, tool=GESTURE_TOOL, gesture="wave"), id="a gesture"),
            pytest.param(
                lambda scene: scene.call.call(scene.token, SAY, {"room": scene.owner.room_id, "text": "Hallo"}),
                id="a said line",
            ),
        ],
    )
    def test_call_73_every_seat_is_told_the_outcome_cleared(self, stopped, ending):
        ending(stopped)

        for seat in (stopped.owner, stopped.member):
            assert turn_state_told(seat) == (stopped.agent_id, None)

    def test_call_73_a_later_seat_is_told_no_outcome_once_cleared(self, stopped):
        call_in(stopped, tool=SET_TURN_STATE_TOOL)
        stopped.owner.frame_of_type(TURN_STATE)
        _, later_login = stopped.call.member()
        later = stopped.call.seat(later_login, stopped.owner.room_id)
        later.frame_of_type(JOINED)
        call_in(stopped, tool=SET_TURN_STATE_TOOL, thinking=True)

        assert thinking_told(later) == (stopped.agent_id, True)
        assert [frame for frame in later.received if frame["type"] == TURN_STATE] == []
