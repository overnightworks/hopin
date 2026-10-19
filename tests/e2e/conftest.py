import http
import http.client
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

SOURCE_DIRECTORY = Path(__file__).resolve().parents[2] / "src"
LOOPBACK_HOST = "127.0.0.1"
HEALTH_PATH = "/health"
SERVER_LOG_NAME = "server.log"
READINESS_POLL_INTERVAL_SECONDS = 0.1
READINESS_DEADLINE_SECONDS = 20
SERVER_STOP_TIMEOUT_SECONDS = 10
FAKE_MEDIA_BROWSER_ARGS = [
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
]
MEDIA_PERMISSIONS = ["camera", "microphone"]
MEDIA_FLOWING_TIMEOUT_MILLISECONDS = 30_000
INVITE_LINK_PATTERN = re.compile(r"/c/\w+")
DESKTOP = {"viewport": {"width": 1280, "height": 800}}
PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}
SMALL_PHONE_LANDSCAPE = {"viewport": {"width": 568, "height": 320}, "is_mobile": True, "has_touch": True}
RECORD_PEER_CONNECTIONS = """
window.recordedConfigurations = [];
window.recordedConnections = [];
const NativePeerConnection = window.RTCPeerConnection;
window.RTCPeerConnection = function (configuration, ...rest) {
    window.recordedConfigurations.push(configuration);
    const connection = new NativePeerConnection(configuration, ...rest);
    window.recordedConnections.push(connection);
    return connection;
};
window.RTCPeerConnection.prototype = NativePeerConnection.prototype;
"""
OPEN_PEER_COUNT_REACHED = """
(count) => window.recordedConnections.filter((connection) => connection.signalingState !== "closed").length === count
"""
LOCAL_VIDEO_PLAYING = "() => document.getElementById('localVideo').videoWidth > 0"


def _reserve_free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOOPBACK_HOST, 0))
        return int(probe.getsockname()[1])


def _is_healthy(port: int) -> bool:
    connection = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=READINESS_POLL_INTERVAL_SECONDS * 10)
    try:
        connection.request("GET", HEALTH_PATH)
        return connection.getresponse().status == http.HTTPStatus.OK
    except OSError:
        return False
    finally:
        connection.close()


def _wait_until_healthy(server: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + READINESS_DEADLINE_SECONDS
    while time.monotonic() < deadline:
        if server.poll() is not None:
            pytest.fail(f"HopIn exited with code {server.returncode} before becoming healthy")
        if _is_healthy(port):
            return
        time.sleep(READINESS_POLL_INTERVAL_SECONDS)
    pytest.fail(f"HopIn was not healthy within {READINESS_DEADLINE_SECONDS} seconds")


def _stop(server: subprocess.Popen[bytes]) -> None:
    server.terminate()
    try:
        server.wait(timeout=SERVER_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()


@pytest.fixture(scope="session")
def base_url(pytestconfig: pytest.Config, delete_output_dir: None) -> Iterator[str]:
    port = _reserve_free_port()
    log_directory = Path(pytestconfig.getoption("output"))
    log_directory.mkdir(parents=True, exist_ok=True)
    environment = {"PYTHONPATH": str(SOURCE_DIRECTORY), "HOPIN_HOST": LOOPBACK_HOST, "HOPIN_PORT": str(port)}
    with (log_directory / SERVER_LOG_NAME).open("wb") as server_log:
        # The command is built from sys.executable and constants, never from input.
        server = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "hopin.main"],
            env=environment,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_until_healthy(server, port)
            yield f"http://{LOOPBACK_HOST}:{port}"
        finally:
            _stop(server)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_type_launch_args, "args": FAKE_MEDIA_BROWSER_ARGS}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args: dict[str, Any]) -> dict[str, Any]:
    return {**browser_context_args, "permissions": MEDIA_PERMISSIONS}


@pytest.fixture
def open_person(browser: Browser, browser_context_args: dict[str, Any]) -> Iterator[Callable[..., Page]]:
    contexts: list[BrowserContext] = []

    def open_page(**device_options: Any) -> Page:
        context = browser.new_context(**{**browser_context_args, **device_options})
        contexts.append(context)
        return context.new_page()

    yield open_page
    for context in contexts:
        context.close()


def press_start_call(host: Page, base_url: str) -> None:
    host.goto(f"{base_url}/")
    host.click("#homeStartBtn")
    host.click("#setupStartBtn")


def start_call(host: Page, base_url: str) -> str:
    press_start_call(host, base_url)
    expect(host.locator("#shareUrl")).to_have_value(INVITE_LINK_PATTERN)
    host.wait_for_function(LOCAL_VIDEO_PLAYING, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
    return host.input_value("#shareUrl")


def wait_for_peer_count(person: Page, count: int) -> None:
    person.wait_for_function(OPEN_PEER_COUNT_REACHED, arg=count, timeout=MEDIA_FLOWING_TIMEOUT_MILLISECONDS)
