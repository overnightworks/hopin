import base64
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from hopin.participation.vocabulary import PERSONA_ROOM_PATH
from tests.e2e.support.dialed_agent import AGENT_TILE
from tests.e2e.support.people import FORM_FACTORS
from tests.participation.support.figures import FigureOrigin, digest_of, figure_url, glb, ok

if TYPE_CHECKING:
    from collections.abc import Callable

    from playwright.sync_api import Page, Response

    from tests.e2e.support.participation_scene import Scene
    from tests.e2e.support.server import RunningServer

pytestmark = pytest.mark.e2e

FIGURE_FILES = Path(__file__).parent / "figure_files"
# The figure is figure's conformant synthetic one; the room is one unlit backdrop quad of magenta, a colour no figure
# or page part has, and it passes figure's room check.
DRAWABLE = (FIGURE_FILES / "drawable.vrm").read_bytes()
MAGENTA_ROOM = (FIGURE_FILES / "magenta_room.glb").read_bytes()
DECLARED_ROOM = glb(b"declared room")
# The server passes it as a room, it has a glTF binary header; figure's own check refuses it, it is no scene.
REFUSED_ROOM = glb(b"salieri" * 8)
DRAWABLE_URL = figure_url("drawable.vrm")
ROOM_URL = figure_url("magenta_room.glb")
SWAPPED_ROOM_URL = figure_url("swapped_room.glb")
REFUSED_ROOM_URL = figure_url("refused_room.glb")
WAIT_FOR_THE_FIGURE_MILLISECONDS = 30_000
WAIT_FOR_THE_ROOM_SECONDS = 30
# The held page clock advances by a step to each picture.
STEP_MILLISECONDS = 100
CLOCK_LEAD_MILLISECONDS = 5_000
SCREENSHOT_ATTEMPTS = 3
SCREENSHOT_TIMEOUT_MILLISECONDS = 5_000

ROOM_ANSWER_POLL_MILLISECONDS = 250

# Whether the page's fetch of a room has finished, and the tasks that read its body have run: the load of a fetch is
# entered in the page's resource timing once its response is in, and a message through a channel is a task behind the
# tasks already queued. Neither needs the page's faked clock.
ROOM_BYTES_READ = """
async (roomPath) => {
    const loaded = performance
        .getEntriesByType("resource")
        .filter((entry) => new URL(entry.name).pathname.startsWith(roomPath));
    if (loaded.length === 0 || loaded.some((entry) => entry.responseEnd === 0)) return false;
    for (let turn = 0; turn < 3; turn++) {
        await new Promise((resolve) => {
            const channel = new MessageChannel();
            channel.port1.onmessage = resolve;
            channel.port2.postMessage(null);
        });
    }
    return true;
}
"""

# The number of pixels of the canvas as the page shows it in the room's colour: pure magenta, which the figure's own
# pink is far from.
MAGENTA_PIXELS = """
async (base64) => {
    // The page's policy forbids fetching a data URL, so the bytes become a blob without one.
    const bytes = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
    const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    const context = canvas.getContext("2d");
    context.drawImage(bitmap, 0, 0);
    const { data } = context.getImageData(0, 0, bitmap.width, bitmap.height);
    let count = 0;
    for (let at = 0; at < data.length; at += 4) {
        const [red, green, blue, alpha] = data.subarray(at, at + 4);
        if (alpha >= 200 && red >= 200 && blue >= 200 && green <= 40) count++;
    }
    return count;
}
"""


@pytest.fixture(scope="module")
def figure_origin() -> FigureOrigin:
    """The far side of the figure transport: the figure and the rooms, one of them served with other bytes than its
    digest names."""
    origin = FigureOrigin()
    origin.serve(DRAWABLE_URL, ok(DRAWABLE))
    origin.serve(ROOM_URL, ok(MAGENTA_ROOM))
    origin.serve(SWAPPED_ROOM_URL, ok(MAGENTA_ROOM))
    origin.serve(REFUSED_ROOM_URL, ok(REFUSED_ROOM))
    return origin


@pytest.fixture
def e2e_server(figure_server: RunningServer) -> RunningServer:
    return figure_server


@pytest.fixture
def room_answers() -> dict[Page, list[Response]]:
    """The answers each page got for the rooms it asked the server for."""
    return {}


@pytest.fixture
def open_person(open_person: Callable[..., Page], room_answers: dict[Page, list[Response]]) -> Callable[..., Page]:
    """Opens each page recording its room answers and with its clock faked from its first load, so a test holds the
    clock once the figure is drawn: a figure that draws on in real time ends as too slow when a loaded machine draws
    every browser's figure at once. The faked clock runs with real time until then."""

    def open_recording(**options: Any) -> Page:
        page = open_person(**options)
        page.clock.install()
        answers = room_answers.setdefault(page, [])
        page.on(
            "response",
            lambda response: (
                answers.append(response) if urlsplit(response.url).path.startswith(PERSONA_ROOM_PATH) else None
            ),
        )
        return page

    return open_recording


def figure_look_with(room_url: str, room: bytes) -> dict[str, Any]:
    return {
        "kind": "figure",
        "url": DRAWABLE_URL,
        "sha256": digest_of(DRAWABLE),
        "room": {"url": room_url, "sha256": digest_of(room)},
    }


def magenta_pixels(person: Page) -> int:
    """The magenta pixels of the figure's canvas; a tile that is laid out anew draws its figure afresh, so a canvas that
    is gone is looked up again."""
    for attempt in range(SCREENSHOT_ATTEMPTS):
        try:
            person.clock.run_for(STEP_MILLISECONDS)
            picture = person.locator(f"{AGENT_TILE} canvas").screenshot(timeout=SCREENSHOT_TIMEOUT_MILLISECONDS)
        except PlaywrightError:
            if attempt == SCREENSHOT_ATTEMPTS - 1:
                raise
            person.locator(f"{AGENT_TILE} canvas").wait_for(timeout=SCREENSHOT_TIMEOUT_MILLISECONDS)
        else:
            return int(person.evaluate(MAGENTA_PIXELS, base64.b64encode(picture).decode()))
    raise AssertionError("unreachable")


def room_is_seen(person: Page) -> bool:
    """Whether the room's colour shows in the person's figure before the wait is over."""
    deadline = time.monotonic() + WAIT_FOR_THE_ROOM_SECONDS
    while time.monotonic() < deadline:
        try:
            if magenta_pixels(person) > 0:
                return True
        except PlaywrightError:
            return False
    return False


def room_statuses(person: Page, room_answers: dict[Page, list[Response]]) -> list[int]:
    return [answer.status for answer in room_answers[person]]


def wait_for_the_room_answer(person: Page, room_answers: dict[Page, list[Response]]) -> None:
    """Returns once the page has read the room's bytes, so the figure has been handed them. The wait ends when the bytes
    are in; figure's own check and parse of the room finish on their own, without the page clock. The page's clock is
    faked, so its own timers and frames do not run; the answers are read from the recorded responses and the page from
    here."""
    deadline = time.monotonic() + WAIT_FOR_THE_ROOM_SECONDS
    while not (room_answers[person] and person.evaluate(ROOM_BYTES_READ, PERSONA_ROOM_PATH)):
        if time.monotonic() > deadline:
            raise AssertionError("the page did not read the room's bytes")
        person.wait_for_timeout(ROOM_ANSWER_POLL_MILLISECONDS)


def figure_state(person: Page) -> tuple[str | None, str | None]:
    vid = person.locator(AGENT_TILE).locator(".vid")
    return vid.get_attribute("data-figure"), vid.get_attribute("data-figure-end")


def close_the_more_menu(person: Page) -> None:
    """The adder's more menu stays open after it adds the agent; on a phone it is a sheet with a scrim that dims the
    tile, so the room's colour is read only once it is closed."""
    menu = person.locator("#moreMenu")
    if menu.is_visible():
        person.keyboard.press("Escape")
    expect(menu).to_be_hidden()


def with_the_figure_drawn(scene: Scene, look: dict[str, Any]) -> list[Page]:
    """The agent is accepted with the look and a late joiner is in: each browser of the call with the figure drawn."""
    scene.member_adds_and_the_agent_accepts(looks=[look])
    everyone = [*scene.everyone, scene.person_joins_late()]
    for person in everyone:
        expect(person.locator(AGENT_TILE).locator(".vid")).to_have_attribute(
            "data-figure", "drawn", timeout=WAIT_FOR_THE_FIGURE_MILLISECONDS
        )
        close_the_more_menu(person)
        person.clock.pause_at(timedelta(milliseconds=CLOCK_LEAD_MILLISECONDS) + datetime.now(tz=UTC))
    return everyone


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
def test_a_personas_room_shows_beside_its_figure_in_every_browser_the_late_joiner_included(
    scene_of: Callable[..., Scene], room_answers: dict[Page, list[Response]], form_factor: dict[str, Any]
) -> None:
    """CALL-80: The room a persona's figure look names shows behind its figure on its tile in every browser of the call,
    late joiners included."""
    scene = scene_of(form_factor)

    everyone = with_the_figure_drawn(scene, figure_look_with(ROOM_URL, MAGENTA_ROOM))

    seen = [room_is_seen(person) for person in everyone]
    assert seen == [True] * len(everyone), (
        [figure_state(person) for person in everyone],
        [room_statuses(person, room_answers) for person in everyone],
    )
    assert [room_statuses(person, room_answers) for person in everyone] == [[200]] * len(everyone)


@pytest.mark.parametrize("form_factor", FORM_FACTORS)
@pytest.mark.parametrize(
    ("room_url", "declared", "answer"),
    [
        pytest.param(SWAPPED_ROOM_URL, DECLARED_ROOM, 502, id="a room whose digest does not match"),
        pytest.param(REFUSED_ROOM_URL, REFUSED_ROOM, 200, id="a room figure refuses"),
    ],
)
def test_a_room_that_cannot_be_shown_leaves_no_room_and_the_figure_drawn(
    scene_of: Callable[..., Scene],
    room_answers: dict[Page, list[Response]],
    form_factor: dict[str, Any],
    room_url: str,
    declared: bytes,
    answer: int,
) -> None:
    """CALL-80: A room whose bytes do not match its digest, or that figure's check refuses, shows no room and the
    figure is drawn all the same. What is observed is the route's answer and the figure's drawn state; the swapped room
    is the magenta one, so a server that ignored the digest would show it."""
    scene = scene_of(form_factor)

    everyone = with_the_figure_drawn(scene, figure_look_with(room_url, declared))
    for person in everyone:
        wait_for_the_room_answer(person, room_answers)
    pixels = [magenta_pixels(person) for person in everyone]

    assert [room_statuses(person, room_answers) for person in everyone] == [[answer]] * len(everyone)
    assert pixels == [0] * len(everyone)
    assert [figure_state(person) for person in everyone] == [("drawn", None)] * len(everyone)
