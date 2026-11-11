import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect

from hopin.config.constants import APP_ASSET_PATH, IMMUTABLE_ASSET_DIRECTORY
from hopin.participation.settings import ParticipationSettings
from hopin.web.page_build import BUILD_DIRECTORY
from tests.e2e.support.dialed_agent import ADDERS_GROUP, AGENT_TILE, OWNER_SUBJECT
from tests.e2e.support.people import DESKTOP
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin
from tests.participation.support.figures import SOURCE_PREFIX, Answer, FigureOrigin, digest_of, figure_url, glb, ok

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tests.e2e.support.participation_scene import Scene

pytestmark = pytest.mark.e2e

FRONTEND_DIRECTORY = Path(__file__).parents[3] / "frontend"
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


def a_refusal_code_of_the_package() -> str:
    """A code the package exports; the page's chunk that holds the package's code holds it too."""
    script = (
        "import { refusalDescriptions } from 'overnightworks-figure/codes';"
        " console.log(Object.keys(refusalDescriptions)[0])"
    )
    code = subprocess.run(  # noqa: S603 - a fixed command in the repository's own frontend
        ["node", "--input-type=module", "-e", script],  # noqa: S607 - the CI job and a developer's shell have node
        cwd=FRONTEND_DIRECTORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert code
    return code


def chunks_with_the_figure_code(base_url: str) -> set[str]:
    """The URLs of the page build's scripts that hold the package's code."""
    code = a_refusal_code_of_the_package()
    immutable = BUILD_DIRECTORY / APP_ASSET_PATH.removeprefix("/") / IMMUTABLE_ASSET_DIRECTORY
    return {
        f"{base_url}{APP_ASSET_PATH}/{IMMUTABLE_ASSET_DIRECTORY}/{script.relative_to(immutable).as_posix()}"
        for script in immutable.rglob("*.js")
        if code in script.read_text(encoding="utf-8")
    }


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
        assert chunks_with_the_figure_code(e2e_server.base_url) <= set(person.evaluate(LOADED_FILES))


@pytest.mark.parametrize("figures_on", [False])
def test_with_figures_off_no_file_that_holds_the_figure_code_is_requested(
    scene_of: Callable[[dict[str, Any]], Scene], e2e_server: RunningServer
) -> None:
    """CALL-56: Without a figure source the page never requests the figure chunk, whoever has a figure to name."""
    scene = scene_of(DESKTOP)

    scene.member_adds_and_the_agent_accepts(figure_of(UNDRAWABLE_URL, UNDRAWABLE))

    figure_chunks = chunks_with_the_figure_code(e2e_server.base_url)
    assert figure_chunks
    for person in scene.everyone:
        expect(person.locator(AGENT_TILE)).to_have_count(1)
        assert person.locator(AGENT_TILE + " .vid[data-figure]").count() == 0
        assert not figure_chunks & set(person.evaluate(LOADED_FILES))
