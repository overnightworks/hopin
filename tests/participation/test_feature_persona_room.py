"""A persona's room at HopIn's real `/mcp`, `/ws` and room route: the allowlist at accept, the path every seat is told,
and the route that serves the verified file while a joined agent names it as its room."""

import pytest
import structlog.testing

from hopin.config.constants import IMMUTABLE_ASSET_CACHE_CONTROL
from hopin.participation.vocabulary import (
    CLIP_MAX_BYTES,
    CLIP_PATH,
    FIGURE_PATH,
    PERSONA_ROOM_MAX_BYTES,
    PERSONA_ROOM_PATH,
    FigureRefusal,
)
from hopin.rooms.reference import room_reference
from tests.participation.support.figure_scene import (
    FILE,
    JOINED,
    ROOM,
    accept_with,
    figure_look,
    read_figure,
    reads_and_origin_fetches,
    refusals_in,
    room_of,
    status_of_a_served_file_once_the_agent_has_left,
)
from tests.participation.support.figures import (
    SOURCE_PREFIX,
    Answer,
    digest_of,
    figure_url,
    glb,
    ok,
    redirect,
)

pytestmark = pytest.mark.integration

REFUSED_EVENT = "agent_persona_room.refused"
RELEASE_ASSETS = "https://release-assets.example.test/personas/room.glb"


@pytest.fixture
def sources():
    return [SOURCE_PREFIX]


def room_url():
    return figure_url("room.glb")


def joined_with_room(scene, figure_origin, body=ROOM, answer=None, url=None):
    """The agent accepted with a figure look whose room the origin answers with `answer` (the room's own bytes when
    none); the path the owner's seat was told for the room."""
    url = url or room_url()
    figure_origin.serve(figure_url(), ok(FILE))
    figure_origin.serve(url, ok(body) if answer is None else answer)
    assert not accept_with(scene, None, looks=[figure_look(figure_url(), room=room_of(url, body))]).is_error
    return scene.owner.frame_of_type(JOINED)["room"]


def is_active(scene):
    return [item["state"] for item in scene.standings()] == ["active"]


class TestTheRoomAtAccept:
    def test_call_80_an_allowed_room_is_told_to_every_seat_and_to_a_later_one_as_the_path_that_serves_it(
        self, scene, figure_origin
    ):
        path = joined_with_room(scene, figure_origin)
        told = [path, scene.member.frame_of_type(JOINED)["room"]]
        _, later_login = scene.call.member()
        later = scene.call.seat(later_login, scene.owner.room_id)
        served = read_figure(scene, path)

        assert told == [path] * 2
        assert later.frame_of_type(JOINED)["room"] == path
        assert path == f"{PERSONA_ROOM_PATH}/{digest_of(ROOM)}"
        assert (served.status_code, served.content) == (200, ROOM)
        assert (served.headers["content-type"], served.headers["x-content-type-options"]) == (
            "model/gltf-binary",
            "nosniff",
        )
        assert served.headers["cache-control"] == IMMUTABLE_ASSET_CACHE_CONTROL

    def test_call_80_a_figure_look_without_a_room_is_told_no_room(self, scene, figure_origin):
        figure_origin.serve(figure_url(), ok(FILE))
        accept_with(scene, None, looks=[figure_look(figure_url())])

        assert "room" not in scene.owner.frame_of_type(JOINED)

    @pytest.mark.parametrize(
        ("url", "code"),
        [
            pytest.param(room_url().replace("figures.", "other."), FigureRefusal.OTHER_HOST, id="another host"),
            pytest.param("https://figures.example.test/elsewhere/room.glb", FigureRefusal.OUTSIDE_PREFIX, id="outside"),
            pytest.param(room_url().replace(".test/", ".test:443/"), FigureRefusal.PORT, id="an explicit port"),
            pytest.param(f"{SOURCE_PREFIX}v0.1.0/%2e%2e/room.glb", FigureRefusal.PERCENT, id="a percent sign"),
        ],
    )
    def test_call_80_a_room_outside_the_allowed_sources_is_dropped_the_figure_stays_and_only_its_code_is_logged(
        self, scene, figure_origin, url, code
    ):
        figure_origin.serve(figure_url(), ok(FILE))
        with structlog.testing.capture_logs() as logs:
            accept_with(scene, None, looks=[figure_look(figure_url(), room=room_of(url))])

        joined = scene.owner.frame_of_type(JOINED)
        assert ("figure" in joined, "room" in joined) == (True, False)
        assert refusals_in(logs, REFUSED_EVENT) == [
            {
                "event": REFUSED_EVENT,
                "room_ref": room_reference(scene.owner.room_id),
                "reason": code.value,
                "log_level": "info",
            }
        ]
        assert url not in str(logs)
        assert figure_origin.requested == []

    def test_call_80_a_room_of_an_agent_whose_figure_is_refused_is_dropped_with_it(self, scene, figure_origin):
        figure_origin.serve(room_url(), ok(ROOM))
        elsewhere = figure_url().replace("figures.", "other.")
        accept_with(scene, None, looks=[figure_look(elsewhere, room=room_of(room_url()))])

        joined = scene.owner.frame_of_type(JOINED)

        assert ("figure" in joined, "room" in joined) == (False, False)
        assert read_figure(scene, f"{PERSONA_ROOM_PATH}/{digest_of(ROOM)}").status_code == 404


class TestTheRoute:
    def test_call_80_a_room_is_fetched_once_for_every_read(self, scene, figure_origin):
        served = reads_and_origin_fetches(scene, figure_origin, joined_with_room(scene, figure_origin), room_url())

        assert served == ([ROOM] * 2, 1)

    def test_call_80_a_room_whose_source_redirects_the_way_github_does_is_served(self, scene, figure_origin):
        released = f"{RELEASE_ASSETS}?sp=r&sig=a%2Bb"
        figure_origin.serve(released, ok(ROOM))

        path = joined_with_room(scene, figure_origin, answer=redirect(released))
        served = read_figure(scene, path)

        assert (served.status_code, served.content) == (200, ROOM)
        assert figure_origin.requested.count(released) == 1

    @pytest.mark.parametrize(
        ("body", "answer"),
        [
            pytest.param(ROOM, Answer(404), id="not found"),
            pytest.param(ROOM, ok(glb(b"another room")), id="a wrong digest"),
            pytest.param(b"not a glb", None, id="no glTF header"),
            pytest.param(
                glb(b"\x00" * PERSONA_ROOM_MAX_BYTES),
                Answer(200, glb(b"\x00" * PERSONA_ROOM_MAX_BYTES)),
                id="over the bound with no length",
            ),
            pytest.param(glb(b"\x00" * PERSONA_ROOM_MAX_BYTES), None, id="over the bound by its length"),
        ],
    )
    def test_call_80_a_room_that_cannot_be_verified_is_a_bad_gateway_that_is_not_kept_and_the_figure_and_agent_stand(
        self, scene, figure_origin, body, answer
    ):
        path = joined_with_room(scene, figure_origin, body, answer)

        reads = [read_figure(scene, path) for _ in range(2)]

        assert [read.status_code for read in reads] == [502, 502]
        assert "immutable" not in reads[0].headers.get("cache-control", "")
        assert figure_origin.requested.count(room_url()) == 2
        joined = next(frame for frame in scene.owner.received if frame["type"] == JOINED)
        assert read_figure(scene, joined["figure"]).content == FILE
        assert is_active(scene)

    def test_call_80_a_room_of_exactly_the_bound_is_served_though_it_is_over_the_clip_bound(self, scene, figure_origin):
        room = glb(b"\x00" * (PERSONA_ROOM_MAX_BYTES - 12))
        assert len(room) == PERSONA_ROOM_MAX_BYTES > CLIP_MAX_BYTES

        served = read_figure(scene, joined_with_room(scene, figure_origin, room))

        assert (served.status_code, served.content) == (200, room)

    @pytest.mark.parametrize("route", [FIGURE_PATH, CLIP_PATH])
    def test_call_80_the_digest_of_a_room_is_not_found_at_the_route_of_another_kind(self, scene, figure_origin, route):
        joined_with_room(scene, figure_origin)

        assert read_figure(scene, f"{route}/{digest_of(ROOM)}").status_code == 404

    def test_call_80_the_digest_of_a_figure_is_not_found_at_the_room_route(self, scene, figure_origin):
        joined_with_room(scene, figure_origin)

        assert read_figure(scene, f"{PERSONA_ROOM_PATH}/{digest_of(FILE)}").status_code == 404

    def test_call_80_once_the_agent_has_gone_its_room_is_not_found_though_the_cache_holds_it(
        self, scene, figure_origin
    ):
        path = joined_with_room(scene, figure_origin)

        assert status_of_a_served_file_once_the_agent_has_left(scene, path) == 404

    def test_call_80_a_read_of_a_room_without_a_session_is_refused_by_the_gate(self, scene, figure_origin):
        path = joined_with_room(scene, figure_origin)

        assert scene.call.client.get(path).status_code == 401
