from functools import partial
from typing import TYPE_CHECKING

import pytest

# Assertion rewriting must be registered before the first import of a tests module.
pytest.register_assert_rewrite("tests")

from tests.support.sign_in_settings import SignInSettings, build_sign_in_settings  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def sign_in_settings(tmp_path: Path) -> Callable[..., SignInSettings]:
    return partial(build_sign_in_settings, tmp_path)
