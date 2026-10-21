from functools import partial
from typing import TYPE_CHECKING

import pytest

from tests.sign_in_settings import SignInSettings, build_sign_in_settings

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def sign_in_settings(tmp_path: Path) -> Callable[..., SignInSettings]:
    return partial(build_sign_in_settings, tmp_path)
