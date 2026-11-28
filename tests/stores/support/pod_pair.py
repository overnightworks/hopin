"""Two `hopin serve` processes with their own ports against one Redis, the way two pods of a deployment run."""

import contextlib
import http.client
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from tests.support.game_environment import GAME_ENVIRONMENT
from tests.support.page_build import FIXTURE_DIRECTORY
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

LOCALHOST = "127.0.0.1"
LAUNCHER = Path(__file__).parent / "serve_hopin.py"
SOURCE_DIRECTORY = Path(__file__).parents[3] / "src"
# The cap ends a pod that outlives its test, even when pytest dies without its teardown.
KILL_AFTER_SECONDS = 10
RUN_CAP_SECONDS = 300
START_WAIT_SECONDS = 60
REQUEST_WAIT_SECONDS = 10
POLL_SECONDS = 0.2
DIE_WITH_PYTEST = ["setpriv", "--pdeathsig", "TERM", "timeout", "-k", str(KILL_AFTER_SECONDS), str(RUN_CAP_SECONDS)]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind((LOCALHOST, 0))
        return probe.getsockname()[1]


@dataclass(frozen=True)
class Pod:
    port: int
    process: subprocess.Popen[bytes]
    log: Path

    def answer(self, path: str, *, method: str = "GET", headers: Mapping[str, str] | None = None) -> tuple[int, bytes]:
        """The status and body of a request from this machine's address, redirects unfollowed."""
        connection = http.client.HTTPConnection(LOCALHOST, self.port, timeout=REQUEST_WAIT_SECONDS)
        try:
            connection.request(method, path, headers={"Host": urlsplit(TEST_PUBLIC_ORIGIN).netloc, **(headers or {})})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def signal_server(self, signal_name: str) -> None:
        """A signal to the server's process, not to the wrapper that caps it."""
        subprocess.run(["pkill", f"-{signal_name}", "-P", str(self.process.pid)], check=True)  # noqa: S603, S607 - fixed arguments

    def kill(self) -> None:
        """End the server with no chance to say goodbye."""
        self.signal_server("KILL")
        self.process.wait()

    def status(self, path: str, *, method: str = "GET", headers: Mapping[str, str] | None = None) -> int:
        return self.answer(path, method=method, headers=headers)[0]

    def wait_until_serving(self, health_path: str) -> None:
        deadline = time.monotonic() + START_WAIT_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"hopin serve exited with {self.process.returncode}:\n{self.log.read_text()[-2000:]}"
                )
            with contextlib.suppress(OSError):
                if self.status(health_path) == http.HTTPStatus.OK:
                    return
            time.sleep(POLL_SECONDS)
        raise RuntimeError(f"hopin serve did not answer in {START_WAIT_SECONDS} s:\n{self.log.read_text()[-2000:]}")


def _start_pod(environment: dict[str, str], directory: Path, name: str, launcher_options: Sequence[str] = ()) -> Pod:
    port = _free_port()
    log = directory / f"{name}.log"
    process = subprocess.Popen(  # noqa: S603 - fixed argument list
        [*DIE_WITH_PYTEST, sys.executable, str(LAUNCHER), str(FIXTURE_DIRECTORY), *launcher_options],
        env={**environment, "HOPIN_HOST": LOCALHOST, "HOPIN_PORT": str(port)},
        stdout=log.open("wb"),
        stderr=subprocess.STDOUT,
    )
    return Pod(port, process, log)


def _stop(pod: Pod) -> None:
    pod.process.terminate()
    try:
        pod.process.wait(KILL_AFTER_SECONDS * 2)
    except subprocess.TimeoutExpired:
        pod.process.kill()
        pod.process.wait()


def _pod_environment(sign_in_environment: dict[str, str], redis_url: str) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(SOURCE_DIRECTORY),
        **sign_in_environment,
        **GAME_ENVIRONMENT,
        "HOPIN_REDIS_URL": redis_url,
    }


@contextlib.contextmanager
def starting_pod(
    sign_in_environment: dict[str, str], redis_url: str, directory: Path, subscription_wait_seconds: float | None = None
) -> Iterator[Pod]:
    """A pod that has been started, whether or not it answers yet; it is stopped on exit.

    `subscription_wait_seconds` replaces production's wait for the first subscription of agents' wakes.
    """
    options = () if subscription_wait_seconds is None else (str(subscription_wait_seconds),)
    pod = _start_pod(_pod_environment(sign_in_environment, redis_url), directory, "starting", options)
    try:
        yield pod
    finally:
        _stop(pod)


@contextlib.contextmanager
def serving_pod_pair(
    sign_in_environment: dict[str, str], redis_url: str, directory: Path, health_path: str
) -> Iterator[tuple[Pod, Pod]]:
    """Both pods answer `health_path` on entry; both are stopped on exit, whatever happens inside."""
    environment = _pod_environment(sign_in_environment, redis_url)
    pods: list[Pod] = []
    try:
        for name in ("first", "second"):
            pods.append(_start_pod(environment, directory, name))
        for pod in pods:
            pod.wait_until_serving(health_path)
        first, second = pods
        yield first, second
    finally:
        for pod in pods:
            _stop(pod)
