from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect

from hopin.participation.settings import ParticipationSettings
from hopin.participation.vocabulary import FIGURE_MODULE
from tests.e2e.support.client_manifest import urls_of_the_chunks_of
from tests.e2e.support.dialed_agent import ADDERS_GROUP, AGENT_TILE, OWNER_SUBJECT
from tests.e2e.support.people import DESKTOP
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.participation.support.figures import SOURCE_PREFIX, Answer, FigureOrigin, digest_of, figure_url, glb, ok

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.e2e.support.participation_scene import Scene

pytestmark = pytest.mark.e2e

LOADED_FILES = "() => performance.getEntriesByType('resource').map((entry) => entry.name)"
UNDRAWABLE = glb(b"mozart" * 8)
UNDRAWABLE_URL = figure_url("undrawable.glb")
MISSING_URL = figure_url("missing.glb")


@pytest.fixture(scope="module")
def figure_origin() -> FigureOrigin:
    """The far side of the figure transport: a file the figure cannot draw and a file the source does not hold."""
    origin = FigureOrigin()
    origin.serve(UNDRAWABLE_URL, ok(UNDRAWABLE))
    origin.serve(MISSING_URL, Answer(404))
    return origin


@pytest.fixture(scope="module")
def figure_server(tmp_path_factory: pytest.TempPathFactory, figure_origin: FigureOrigin) -> Iterator[RunningServer]:
    """HopIn with participation that allows figures from the test source, fetched through `figure_origin`."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("figure-sign-in"))
    with running_hopin(
        loopback,
        tmp_path_factory.mktemp("figure-tls"),
        participation=True,
        agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
        figure_sources=ParticipationSettings(figure_sources=[SOURCE_PREFIX]).figure_sources,
        figure_transport=figure_origin,
    ) as server:
        yield server


@pytest.fixture
def figures_on() -> bool:
    return True


@pytest.fixture
def e2e_server(figures_on: bool, figure_server: RunningServer, participation_server: RunningServer) -> RunningServer:
    return figure_server if figures_on else participation_server


def figure_chunk_urls(base_url: str) -> set[str]:
    """The URLs of the page build's files that hold the figure code: the feature's chunk and the package's."""
    return {f"{base_url}{url}" for url in urls_of_the_chunks_of(FIGURE_MODULE)}


def figure_of(url: str, body: bytes) -> dict[str, str]:
    return {"url": url, "sha256": digest_of(body)}


@pytest.mark.parametrize(
    "figure",
    [
        pytest.param(figure_of(UNDRAWABLE_URL, UNDRAWABLE), id="a file the figure cannot draw"),
        pytest.param(figure_of(MISSING_URL, b""), id="a file the source does not hold"),
    ],
)
def test_a_failed_figure_leaves_the_symbol_and_no_canvas(
    scene_of: Callable[[dict[str, Any]], Scene], e2e_server: RunningServer, figure: dict[str, str]
) -> None:
    """CALL-56: A figure that cannot be drawn or fetched ends, and the tile shows its symbol with no canvas."""
    scene = scene_of(DESKTOP)

    scene.member_adds_and_the_agent_accepts(figure)

    for person in scene.everyone:
        tile = person.locator(AGENT_TILE)
        expect(tile.locator(".vid")).to_have_attribute("data-figure", "ended")
        expect(tile.locator(".av")).to_be_visible()
        expect(tile.locator("canvas")).to_have_count(0)
        assert figure_chunk_urls(e2e_server.base_url) <= set(person.evaluate(LOADED_FILES))


@pytest.mark.parametrize("figures_on", [False])
def test_with_figures_off_no_file_that_holds_the_figure_code_is_requested(
    scene_of: Callable[[dict[str, Any]], Scene], e2e_server: RunningServer
) -> None:
    """CALL-56: Without a figure source the page never requests the figure chunk, whoever has a figure to name."""
    scene = scene_of(DESKTOP)

    scene.member_adds_and_the_agent_accepts(figure_of(UNDRAWABLE_URL, UNDRAWABLE))

    figure_chunks = figure_chunk_urls(e2e_server.base_url)
    assert figure_chunks
    for person in scene.everyone:
        expect(person.locator(AGENT_TILE)).to_have_count(1)
        assert person.locator(AGENT_TILE + " .vid[data-figure]").count() == 0
        assert not figure_chunks & set(person.evaluate(LOADED_FILES))
