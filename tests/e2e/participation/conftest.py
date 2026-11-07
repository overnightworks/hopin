from typing import TYPE_CHECKING

import pytest

from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def participation_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningServer]:
    """HopIn with participation wired into its calls."""
    loopback = loopback_sign_in(tmp_path_factory.mktemp("participation-sign-in"))
    with running_hopin(loopback, tmp_path_factory.mktemp("participation-tls"), participation=True) as server:
        yield server


@pytest.fixture
def e2e_server(participation_server: RunningServer) -> RunningServer:
    return participation_server
