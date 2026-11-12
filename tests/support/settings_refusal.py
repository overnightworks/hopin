"""The refusal of a malformed setting: the settings do not load, and the message holds no input."""

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from pydantic_settings import BaseSettings


def assert_refused_without_echo(settings_type: type[BaseSettings], input_value: str) -> None:
    with pytest.raises(ValidationError) as refusal:
        settings_type()

    assert input_value not in str(refusal.value)
