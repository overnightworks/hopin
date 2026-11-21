"""The fixtures of the figure tests: the figure origin behind the transport port, the sources it is allowed at and the
scene of an agent joined with a shown figure."""

import pytest

from hopin.participation.settings import ParticipationSettings
from tests.participation.support.figure_scene import FILE, JOINED, joined_with
from tests.participation.support.figures import SOURCE_PREFIX, FigureOrigin, figure_url, ok


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
def gesturing(scene, figure_origin):
    """The scene with the agent joined with a shown figure."""
    joined_with(scene, figure_origin.serve(figure_url(), ok(FILE)))
    scene.agent_id = scene.member.frame_of_type(JOINED)["agent_id"]
    return scene
