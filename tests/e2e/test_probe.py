import json
import os
import re
import secrets
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from agents.member import SPEAK_TOOL
from speech_client.wire import VoiceId
from starlette.applications import Starlette
from starlette.responses import Response
from starlette.routing import Route

from hopin_probe import main as probe
from hopin_probe.settings import DEFAULT_VOICE_FILE
from tests.e2e.support.dialed_agent import OWNER_SUBJECT, OWNER_USERNAME, OwnAccountAgent
from tests.e2e.support.participation_scene import INVITATION_WAKE_SECONDS
from tests.e2e.support.probe_stack import (
    LOCKED_USERNAME,
    STRANGER_USERNAME,
    TERMS_USERNAME,
    ProbeStack,
    https_server,
    probe_stack,
)
from tests.e2e.support.server import (
    LOOPBACK_HOST,
    bound_loopback_socket,
    port_of,
    serving,
    write_self_signed_certificate,
)
from tests.support.loopback_origin import Origin
from tests.support.speech_origin import chunked_wav, offering, silent_wav, speaking

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

pytestmark = pytest.mark.e2e

FETCH_VOICE = Path(__file__).parents[2] / "scripts" / "fetch_probe_voice.sh"
# The servers of the stack are self-signed.
TRUST_THE_STACK = ["--ignore-certificate-errors"]
NOT_REACHED = [f"check={check} verdict=no reason=not_reached" for check in "ABC"]
JOINED_AND_ANSWERED = [
    r"check=A verdict=yes seconds=\d+",
    "check=B verdict=yes",
    r"check=C verdict=yes seconds=\d+",
]
SOUND_SECONDS = 30
JOIN_SECONDS = 20
QUICK_SECONDS = 4


@pytest.fixture(scope="session")
def voice_file() -> Path:
    """The voice the probe speaks with, fetched once into the user's cache by the script the image uses."""
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    directory = cache / "hopin-probe" / "voice"
    subprocess.run([str(FETCH_VOICE), str(directory)], check=True)  # noqa: S603
    return directory / DEFAULT_VOICE_FILE.name


@pytest.fixture(scope="module")
def speech_origin() -> Iterator[Origin]:
    origin = Origin()
    offering(origin, *VoiceId)
    yield origin
    origin.close()


@pytest.fixture(scope="module")
def speaking_stack(tmp_path_factory: pytest.TempPathFactory, speech_origin: Origin) -> Iterator[ProbeStack]:
    with probe_stack(tmp_path_factory, "probe-speaking", speech_origin=speech_origin.base_url, hears=True) as stack:
        yield stack


@pytest.fixture(scope="module")
def muted_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ProbeStack]:
    """The call's agents have no hearing switch and cannot speak."""
    with probe_stack(tmp_path_factory, "probe-muted") as stack:
        yield stack


@pytest.fixture(autouse=True)
def restore_the_excepthook(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` installs its own excepthook; the suite's comes back after each test."""
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)


@pytest.fixture
def configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, voice_file: Path) -> Callable[..., tuple[str, str]]:
    """Sets the probe's configuration in the environment and returns the agent's name and the password it was given."""

    def set_up(
        stack: ProbeStack,
        *,
        username: str = OWNER_USERNAME,
        password: str | None = None,
        origin: str | None = None,
        join_seconds: int = JOIN_SECONDS,
        answer_seconds: int = QUICK_SECONDS,
    ) -> tuple[str, str]:
        agent = f"Darwin-{secrets.token_hex(3)}"
        password = stack.password if password is None else password
        password_file = tmp_path / "password"
        password_file.write_text(f"{password}\n")
        for name, value in {
            "ORIGIN": stack.server.base_url if origin is None else origin,
            "USERNAME": username,
            "PASSWORD_FILE": str(password_file),
            "AGENTS": json.dumps([agent]),
            "VOICE_FILE": str(voice_file),
            "JOIN_SECONDS": str(join_seconds),
            "ANSWER_SECONDS": str(answer_seconds),
        }.items():
            monkeypatch.setenv(f"HOPIN_PROBE_{name}", value)
        return agent, password

    return set_up


def run_probe(capsys: pytest.CaptureFixture[str], secrets_of_the_run: tuple[str, ...]) -> tuple[int, list[str]]:
    """Run the probe's command in this process; nothing it printed holds a name or a credential of the run."""
    exit_code = probe.main(TRUST_THE_STACK)
    captured = capsys.readouterr()
    for secret in secrets_of_the_run:
        assert secret not in captured.out + captured.err
    return exit_code, captured.out.splitlines()


def assert_lines(lines: list[str], expected: list[str]) -> None:
    assert len(lines) == len(expected), lines
    for line, pattern in zip(lines, expected, strict=True):
        assert re.fullmatch(pattern, line), line


@contextmanager
def agent_that(
    answer: Callable[[OwnAccountAgent], None] | None, stack: ProbeStack, name: str
) -> Iterator[OwnAccountAgent]:
    """The owner's scripted agent, listening so the call offers it, and answering the call as `answer` scripts."""
    agent = OwnAccountAgent(stack.server, OWNER_SUBJECT, name)
    failures: list[BaseException] = []

    def answering() -> None:
        try:
            assert answer is not None
            answer(agent)
        except BaseException as failure:
            failures.append(failure)

    try:
        agent.listen()
        answerer = threading.Thread(target=answering) if answer is not None else None
        if answerer is not None:
            answerer.start()
        yield agent
        if answerer is not None:
            answerer.join(INVITATION_WAKE_SECONDS)
    finally:
        agent.close()
    assert failures == []


def accept_and_speak(speech_origin: Origin) -> Callable[[OwnAccountAgent], None]:
    def answer(agent: OwnAccountAgent) -> None:
        invitation = agent.invitation_within(INVITATION_WAKE_SECONDS)
        agent.accept(invitation)
        speaking(speech_origin, chunked_wav(silent_wav(SOUND_SECONDS)))
        agent.call(SPEAK_TOOL, {"place": invitation["place"], "text": "Guten Tag"})

    return answer


def accept_silently(agent: OwnAccountAgent) -> None:
    agent.accept(agent.invitation_within(INVITATION_WAKE_SECONDS))


def decline_as_busy(agent: OwnAccountAgent) -> None:
    agent.decline_busy(agent.invitation_within(INVITATION_WAKE_SECONDS))


def test_an_agent_that_joins_may_hear_and_speaks_is_three_yes_lines_and_exit_0(
    speaking_stack: ProbeStack,
    speech_origin: Origin,
    configure: Callable[..., tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    name, password = configure(speaking_stack)
    with agent_that(accept_and_speak(speech_origin), speaking_stack, name):
        exit_code, lines = run_probe(capsys, (name, password, OWNER_USERNAME))

    assert exit_code == 0
    assert_lines(lines, JOINED_AND_ANSWERED)


def test_an_agent_that_joins_without_a_hearing_switch_and_never_speaks_is_no_on_b_and_c(
    muted_stack: ProbeStack, configure: Callable[..., tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    name, password = configure(muted_stack)
    with agent_that(accept_silently, muted_stack, name):
        exit_code, lines = run_probe(capsys, (name, password, OWNER_USERNAME))

    assert exit_code == 1
    assert_lines(
        lines,
        [r"check=A verdict=yes seconds=\d+", "check=B verdict=no reason=missing", "check=C verdict=no reason=none"],
    )


def test_an_agent_that_is_not_offered_is_no_on_a_with_no_row_and_the_rest_not_reached(
    muted_stack: ProbeStack, configure: Callable[..., tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    """The stranger is in no adders group, so the call never offers it the owner's agent."""
    name, password = configure(muted_stack, username=STRANGER_USERNAME, join_seconds=QUICK_SECONDS)
    with agent_that(None, muted_stack, name):
        exit_code, lines = run_probe(capsys, (name, password, STRANGER_USERNAME))

    assert exit_code == 1
    assert lines == ["check=A verdict=no reason=no_row", *NOT_REACHED[1:]]


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        pytest.param(None, "no_answer", id="never-answers"),
        pytest.param(decline_as_busy, "busy", id="declines-as-busy"),
    ],
)
def test_an_agent_that_is_offered_and_does_not_join_is_no_on_a_with_the_reason_its_row_shows(
    muted_stack: ProbeStack,
    configure: Callable[..., tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
    answer: Callable[[OwnAccountAgent], None] | None,
    reason: str,
) -> None:
    name, password = configure(muted_stack, join_seconds=10)
    with agent_that(answer, muted_stack, name):
        exit_code, lines = run_probe(capsys, (name, password, OWNER_USERNAME))

    assert exit_code == 1
    assert lines == [f"check=A verdict=no reason={reason}", *NOT_REACHED[1:]]


@pytest.mark.parametrize(
    ("username", "right_password", "reason"),
    [
        pytest.param(OWNER_USERNAME, False, "refused", id="wrong-password"),
        pytest.param(LOCKED_USERNAME, True, "refused", id="locked"),
        pytest.param(TERMS_USERNAME, True, "required_action", id="terms-required"),
    ],
)
def test_a_sign_in_the_issuer_does_not_complete_ends_the_run_after_one_attempt(
    muted_stack: ProbeStack,
    configure: Callable[..., tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
    username: str,
    right_password: bool,
    reason: str,
) -> None:
    muted_stack.attempts.clear()
    wrong = None if right_password else secrets.token_urlsafe(8)
    _, password = configure(muted_stack, username=username, password=wrong)

    exit_code, lines = run_probe(capsys, (password, username))

    assert exit_code == 2
    assert lines == [f"stage=sign_in verdict=failed reason={reason}", *NOT_REACHED]
    assert muted_stack.attempts == [username]


def test_an_origin_nobody_listens_at_ends_the_run_in_the_open_stage(
    muted_stack: ProbeStack, configure: Callable[..., tuple[str, str]], capsys: pytest.CaptureFixture[str]
) -> None:
    closed = bound_loopback_socket()
    origin = f"https://{LOOPBACK_HOST}:{port_of(closed)}"
    closed.close()
    name, password = configure(muted_stack, origin=origin)

    exit_code, lines = run_probe(capsys, (name, password, OWNER_USERNAME))

    assert exit_code == 2
    assert lines == ["stage=open verdict=failed reason=unreachable", *NOT_REACHED]


def test_an_origin_that_answers_with_a_challenge_ends_the_run_in_the_open_stage(
    tmp_path_factory: pytest.TempPathFactory,
    muted_stack: ProbeStack,
    configure: Callable[..., tuple[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    def challenge(_request: object) -> Response:
        return Response("Just a moment", status_code=403, headers={"cf-mitigated": "challenge"})

    listener = bound_loopback_socket()
    certificate = write_self_signed_certificate(tmp_path_factory.mktemp("challenge-tls"))
    server = https_server(Starlette(routes=[Route("/", challenge)]), certificate)
    name, password = configure(muted_stack, origin=f"https://{LOOPBACK_HOST}:{port_of(listener)}")

    with serving(server, listener):
        exit_code, lines = run_probe(capsys, (name, password, OWNER_USERNAME))

    assert exit_code == 2
    assert lines == ["stage=open verdict=failed reason=challenge", *NOT_REACHED]
