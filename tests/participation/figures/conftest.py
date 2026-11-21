"""What the modules about figures share: the sources the adding call allows, which a case may override as `sources`,
and the transport that serves the figure from the module's `figure_origin`."""

import pytest

from hopin.participation.settings import ParticipationSettings
from tests.participation.support.figures import SOURCE_PREFIX


@pytest.fixture
def sources():
    return [SOURCE_PREFIX]


@pytest.fixture
def figure_sources(sources):
    return ParticipationSettings(figure_sources=sources).figure_sources


@pytest.fixture
def figure_transport(figure_origin):
    return figure_origin
