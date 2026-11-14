"""How fast an agent may act: the times of its lines or looks inside a sliding window. Pure, with the time passed in."""

from dataclasses import dataclass

from hopin.participation.vocabulary import LINE_WINDOW_SECONDS, LINES_PER_WINDOW, LOOK_WINDOW_SECONDS, LOOKS_PER_WINDOW


@dataclass(frozen=True)
class Rate:
    """The most acts a window of seconds holds."""

    most: int
    window_seconds: float


LINE_RATE = Rate(LINES_PER_WINDOW, LINE_WINDOW_SECONDS)
LOOK_RATE = Rate(LOOKS_PER_WINDOW, LOOK_WINDOW_SECONDS)


def admitted(window: tuple[float, ...], now: float, rate: Rate) -> tuple[float, ...] | None:
    """The window with an act at `now` added, or nothing when the window already holds the most acts the rate allows."""
    recent = tuple(acted for acted in window if now - acted < rate.window_seconds)
    if len(recent) >= rate.most:
        return None
    return (*recent, now)
