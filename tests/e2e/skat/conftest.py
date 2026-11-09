from typing import TYPE_CHECKING

import pytest

from hopin.skat.feature import SkatFeature
from tests.e2e.skat.stub_skat import StubSkat, running_stub_skat
from tests.e2e.support.server import RunningServer, loopback_sign_in, running_hopin

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="session")
def stub_skat(tmp_path_factory: pytest.TempPathFactory) -> Iterator[StubSkat]:
    with running_stub_skat(tmp_path_factory.mktemp("stub-skat-tls")) as stub:
        yield stub


@pytest.fixture(scope="session")
def skat_server(stub_skat: StubSkat, tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningServer]:
    """HopIn with Skat wired into its calls, whose tables the stub serves."""
    with running_hopin(
        loopback_sign_in(tmp_path_factory.mktemp("skat-sign-in")),
        tmp_path_factory.mktemp("skat-tls"),
        SkatFeature(stub_skat.origin),
    ) as server:
        yield server


@pytest.fixture
def e2e_server(skat_server: RunningServer) -> RunningServer:
    return skat_server
