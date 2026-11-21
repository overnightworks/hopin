"""The probe's browser: its microphone, the sign-in form and the call page as the checks read it."""

import base64
import json
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

from design.words import text as kit_text
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from hopin.config.constants import AUTH_START_PATH
from hopin.words import text
from hopin_probe.checks import JOINED, Stage, StartFailure, SwitchState

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from pydantic import SecretStr

FAKE_MEDIA_BROWSER_ARGS = [
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]
# The browsers reach no sound server and no ALSA device, so Chromium renders sound into its fake output, as on a CI
# runner. On a machine with a sound server, browsers running in parallel meet its errors, and a page whose AudioContext
# met one captures no microphone sound for its hop. Chromium's `--disable-audio-output` is no substitute: it also stops
# the sound of a remote track, which the speaking ring reads.
NO_SOUND_DEVICE_ENVIRONMENT = {"PULSE_SERVER": "unix:/dev/null", "ALSA_CONFIG_PATH": "/dev/null"}
MEDIA_PERMISSIONS = ["camera", "microphone"]
MEDIA_FLOWING_TIMEOUT_MILLISECONDS = 30_000
LOCALE = "de-DE"
HEARING_SWITCH_SECONDS = 5

PREJOIN_MICROPHONE = "#prejoinMicBtn"
PREJOIN_CAMERA = "#prejoinCamBtn"
PREJOIN_START = "#prejoinStartBtn"
HOME_START = "#homeStartBtn"
OFFER = "#agentOffers li"
AGENT_TILE = '#stage [id^="container-agent-"]'
SPEAKING_AGENT_TILE = f"{AGENT_TILE}.speaking"
# The tile holds the kit's hearing chip and its see chip; each is told by its accessible name.
HEARING_SWITCH_ON_A_TILE = f'role=switch[name="{kit_text("de", "hearing.agentMayHearMe")}"]'
AGENT_HEARING_SWITCH = f"{AGENT_TILE} >> {HEARING_SWITCH_ON_A_TILE}"

# The sign-in form of Keycloak's login page; the field ids are Keycloak's own, not hopin's.
USERNAME_FIELD_ID = "username"
PASSWORD_FIELD_ID = "password"
SIGN_IN_BUTTON_ID = "kc-login"
SIGN_IN_ERROR_ID = "input-error"
# Cloudflare marks the response of a challenge it serves in place of the page with this header.
CHALLENGE_HEADER = "cf-mitigated"
CHALLENGE = "challenge"

SIGNED_IN = "signed_in"
SIGN_IN_RESULT = """
([origin, errorSelector, passwordSelector, signedIn]) => {
    if (location.origin === origin) return signedIn;
    if (document.querySelector(errorSelector)) return "refused";
    return document.querySelector(passwordSelector) ? null : "required_action";
}
"""
# The agent's outcome in the call: its tile with the name and the AI mark, or the words its offer row shows instead.
AGENT_OUTCOME = """
([tileSelector, offerSelector, name, badge, refusals, joined]) => {
    const tile = [...document.querySelectorAll(tileSelector)].find(
        (candidate) => candidate.querySelector(".name")?.textContent.includes(name)
            && candidate.querySelector(".ai-badge")?.textContent === badge);
    if (tile) return joined;
    const row = [...document.querySelectorAll(offerSelector)].find((candidate) => candidate.textContent.includes(name));
    const shown = refusals.find(([words]) => row?.textContent.includes(words));
    return shown ? shown[1] : null;
}
"""
# The offer row's words for a call that does not end with the agent in the call.
REFUSAL_CODES = {
    "agent.noAnswer": "no_answer",
    "agent.busy": "busy",
    "agent.notAllowed": "not_allowed",
    "agent.ownerAbsent": "owner_absent",
    "agent.placeFull": "other",
    "agent.agentUnavailable": "other",
}
# The microphone is a WebAudio graph that plays the clip `say(index)` names; its sound is the page's only microphone.
SAY_ON_DEMAND = """
(() => {
    const clips = __CLIPS__;
    const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    let voice = null;
    window.say = async (index) => {
        const bytes = Uint8Array.from(atob(clips[index]), (character) => character.charCodeAt(0));
        const source = voice.context.createBufferSource();
        source.buffer = await voice.context.decodeAudioData(bytes.buffer);
        source.connect(voice.destination);
        const ended = new Promise((done) => { source.onended = done; });
        source.start();
        await ended;
    };
    navigator.mediaDevices.getUserMedia = async (constraints) => {
        const stream = constraints.video
            ? await nativeGetUserMedia({ video: constraints.video })
            : new MediaStream();
        if (!constraints.audio) return stream;
        if (!voice) {
            const context = new AudioContext();
            voice = { context, destination: context.createMediaStreamDestination() };
        }
        stream.addTrack(voice.destination.stream.getAudioTracks()[0]);
        return stream;
    };
})();
"""


def say_on_demand(clips: Sequence[bytes]) -> str:
    encoded = [base64.b64encode(clip).decode() for clip in clips]
    return SAY_ON_DEMAND.replace("__CLIPS__", json.dumps(encoded))


@contextmanager
def signed_out_page(clips: Sequence[bytes], launch_arguments: Sequence[str]) -> Iterator[Page]:
    """A page of a fresh browser whose microphone plays `clips`; nothing is traced, recorded or forwarded."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=[*FAKE_MEDIA_BROWSER_ARGS, *launch_arguments], env={**os.environ, **NO_SOUND_DEVICE_ENVIRONMENT}
        )
        try:
            context = browser.new_context(permissions=MEDIA_PERMISSIONS, locale=LOCALE)
            context.add_init_script(say_on_demand(clips))
            yield context.new_page()
        finally:
            browser.close()


def open_origin(page: Page, origin: str) -> None:
    try:
        response = page.goto(origin)
    except PlaywrightError:
        raise StartFailure(Stage.OPEN, "unreachable") from None
    if response is not None and response.headers.get(CHALLENGE_HEADER) == CHALLENGE:
        raise StartFailure(Stage.OPEN, CHALLENGE)


def sign_in(page: Page, origin: str, username: str, password: SecretStr) -> None:
    """Sign in once through the issuer's form; a refusal or a required action ends the stage, and nothing is accepted
    on the account's behalf."""
    page.goto(f"{origin}{AUTH_START_PATH}")
    page.fill(f"#{USERNAME_FIELD_ID}", username)
    page.fill(f"#{PASSWORD_FIELD_ID}", password.get_secret_value())
    page.click(f"#{SIGN_IN_BUTTON_ID}")
    result = page.wait_for_function(
        SIGN_IN_RESULT,
        arg=[origin, f"#{SIGN_IN_ERROR_ID}", f"#{PASSWORD_FIELD_ID}", SIGNED_IN],
        timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS,
    ).json_value()
    if result != SIGNED_IN:
        raise StartFailure(Stage.SIGN_IN, result)


def open_prejoin(host: Page, base_url: str) -> None:
    host.goto(f"{base_url}/")
    host.click(HOME_START)


def wait_until_the_prejoin_media_answered(host: Page) -> None:
    expect(host.locator(PREJOIN_START)).to_be_enabled(timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)


def press_through_the_prejoin(person: Page, *, with_camera: bool = True) -> None:
    wait_until_the_prejoin_media_answered(person)
    camera = person.locator(PREJOIN_CAMERA)
    if not with_camera and camera.get_attribute("aria-pressed") == "false":
        camera.click()
        expect(camera).to_have_attribute("aria-pressed", "true")
    person.click(PREJOIN_START)


def wait_until_in_the_call(person: Page) -> None:
    """The server has admitted `person` to the call's room, so a line said from now on reaches them."""
    expect(person.locator("#status")).to_have_text(
        text("de", "call.statusInCall"), timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS
    )


def open_more(person: Page) -> Locator:
    menu = person.locator("#moreMenu")
    if not menu.is_visible():
        person.click("#moreBtn")
    expect(menu).to_be_visible()
    return menu


class AgentSeat:
    """The probe's call page and the one agent it calls."""

    def __init__(self, page: Page, base_url: str, agent: str) -> None:
        self._page = page
        self._base_url = base_url
        self._agent = agent

    def start_call(self) -> None:
        open_prejoin(self._page, self._base_url)
        press_through_the_prejoin(self._page, with_camera=False)
        wait_until_in_the_call(self._page)

    def _offer(self) -> Locator:
        return self._page.locator(OFFER).filter(has_text=self._agent)

    def offer_appears(self, seconds: int) -> bool:
        open_more(self._page)
        try:
            self._offer().wait_for(timeout=seconds * 1000)
        except PlaywrightTimeoutError:
            return False
        return True

    def call_agent(self) -> None:
        self._offer().get_by_role("button", name=kit_text("de", "agent.callLabel", name=self._agent)).click()

    def outcome(self, seconds: int) -> str | None:
        refusals = [[kit_text("de", key), code] for key, code in REFUSAL_CODES.items()]
        arguments = [AGENT_TILE, OFFER, self._agent, kit_text("de", "tile.aiBadge"), refusals, JOINED]
        try:
            return str(self._page.wait_for_function(AGENT_OUTCOME, arg=arguments, timeout=seconds * 1000).json_value())
        except PlaywrightTimeoutError:
            return None

    def hearing_switch(self) -> SwitchState:
        switch = self._page.locator(AGENT_HEARING_SWITCH)
        try:
            switch.wait_for(timeout=HEARING_SWITCH_SECONDS * 1000)
        except PlaywrightTimeoutError:
            return SwitchState.MISSING
        return SwitchState(switch.get_attribute("aria-checked") or SwitchState.MISSING)

    def say_question(self) -> None:
        self._page.evaluate("(index) => window.say(index)", 0)

    def agent_is_speaking(self, seconds: int) -> bool:
        try:
            self._page.wait_for_selector(SPEAKING_AGENT_TILE, timeout=seconds * 1000)
        except PlaywrightTimeoutError:
            return False
        return True
