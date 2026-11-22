"""Waiting for a condition that another thread or process brings about."""

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

WAIT_SECONDS = 20
POLL_SECONDS = 0.05


def wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while not condition():
        assert time.monotonic() < deadline, "the condition did not come about"
        time.sleep(POLL_SECONDS)
