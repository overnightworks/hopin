from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from webauth.ports import RateLimitBackend


@dataclass(frozen=True)
class SharedStores:
    """What the processes of one deployment share, as the app reads it."""

    rate_limits: RateLimitBackend
    # Whether the shared store answers now; `/health` is not ready while it does not.
    answers: Callable[[], bool]
