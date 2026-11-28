from typing import TYPE_CHECKING

import pytest

from hopin.auth.sign_in import build_sign_in_configs
from hopin.config.constants import HEALTH_PATH
from hopin.config.settings import ServerSettings
from hopin.main import main
from tests.stores.support.pod_pair import serving_pod_pair
from tests.stores.support.service_gate import ServiceGate
from tests.support.in_thread_stores import in_thread_stores
from tests.support.sign_in_settings import TEST_REALM

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from webauth.config import WebAuthConfig

    from tests.stores.support.pod_pair import Pod
    from tests.stores.support.scratch_database import ScratchDatabase
    from tests.support.sign_in_settings import SignInSettings


@pytest.fixture
def redis_gate(redis_url: str) -> Iterator[ServiceGate]:
    gate = ServiceGate(redis_url)
    yield gate
    gate.close()


@pytest.fixture
def served_environment(
    sign_in_settings: Callable[..., SignInSettings],
    empty_database: ScratchDatabase,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
    game_environment: None,
    logging_restored: None,
) -> dict[str, str]:
    """The environment `hopin` reads, in this process, over the real Redis and the empty database."""
    environment = sign_in_settings(redis_url=redis_url, **empty_database.settings).environment
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return environment


@pytest.fixture
def migrated_database(empty_database: ScratchDatabase, served_environment: dict[str, str]) -> ScratchDatabase:
    """The database after `hopin migrate`."""
    main(["migrate"])
    return empty_database


@pytest.fixture
def pods(
    sign_in_settings: Callable[..., SignInSettings],
    redis_gate: ServiceGate,
    migrated_database: ScratchDatabase,
    tmp_path: Path,
) -> Iterator[tuple[Pod, Pod]]:
    environment = sign_in_settings(**migrated_database.settings).environment
    with serving_pod_pair(environment, redis_gate.url, tmp_path, HEALTH_PATH) as pair:
        yield pair


@pytest.fixture
def web_auth_config() -> WebAuthConfig:
    return build_sign_in_configs(ServerSettings(), TEST_REALM, (), in_thread_stores().rate_limits).web_auth
