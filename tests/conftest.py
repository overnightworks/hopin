from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from tests.sign_in_settings import SignInSettings, build_sign_in_settings


@pytest.fixture
def sign_in_settings(tmp_path: Path) -> Callable[..., SignInSettings]:
    return partial(build_sign_in_settings, tmp_path)
