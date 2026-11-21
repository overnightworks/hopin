"""An accept's `looks` at HopIn's real `/mcp` and `/ws`: the figure look hopin shows, read by CALLING-41."""

import pytest
from agents.member import GESTURE_TOOL, SET_FRAMING_TOOL

from hopin.participation.vocabulary import FIGURE_PATH, PERSONA_ROOM_PATH
from tests.participation.support.figure_scene import (
    FILE,
    JOINED,
    accept_with,
    figure_look,
    figure_of,
    offered_tools,
    read_figure,
    room_of,
)
from tests.participation.support.figures import SOURCE_PREFIX, digest_of, figure_url, glb, ok

pytestmark = pytest.mark.integration

VIDEO_LOOK = {"kind": "video", "name": "studio"}
OTHER_FILE = glb(b"another figure")


@pytest.fixture
def sources():
    return [SOURCE_PREFIX]


class TestTheLooksOfAnAccept:
    def test_calling_41_looks_with_only_a_video_look_show_no_figure_and_the_accept_stands(self, scene):
        accepted = accept_with(scene, None, looks=[VIDEO_LOOK])

        assert not accepted.is_error
        assert "figure" not in scene.owner.frame_of_type(JOINED)
        assert offered_tools(scene).isdisjoint({GESTURE_TOOL, SET_FRAMING_TOOL})

    def test_calling_41_the_first_figure_look_after_a_video_look_is_shown_with_its_room(self, scene, figure_origin):
        figure_origin.serve(figure_url(), ok(FILE))
        room = room_of(figure_url("room.glb"))
        accept_with(scene, None, looks=[VIDEO_LOOK, figure_look(figure_url(), room=room)])

        joined = scene.owner.frame_of_type(JOINED)

        assert (joined["figure"], joined["room"]) == (
            f"{FIGURE_PATH}/{digest_of(FILE)}",
            f"{PERSONA_ROOM_PATH}/{room['sha256']}",
        )
        assert {GESTURE_TOOL, SET_FRAMING_TOOL} <= offered_tools(scene)

    def test_calling_41_an_accept_with_looks_and_the_old_figure_is_read_by_its_looks_alone(self, scene, figure_origin):
        figure_origin.serve(figure_url(), ok(FILE))
        old = figure_of(figure_url("old.glb"), OTHER_FILE)
        accept_with(scene, old, looks=[figure_look(figure_url())])

        joined = scene.owner.frame_of_type(JOINED)

        assert joined["figure"] == f"{FIGURE_PATH}/{digest_of(FILE)}"
        assert read_figure(scene, f"{FIGURE_PATH}/{digest_of(OTHER_FILE)}").status_code == 404

    def test_calling_41_the_old_figure_alone_is_shown_and_carries_no_room(self, scene, figure_origin):
        figure_origin.serve(figure_url(), ok(FILE))
        accept_with(scene, figure_of(figure_url()))

        joined = scene.owner.frame_of_type(JOINED)

        assert (joined["figure"], "room" in joined) == (f"{FIGURE_PATH}/{digest_of(FILE)}", False)
