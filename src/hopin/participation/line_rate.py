"""How fast an agent may write: the times of its lines inside a sliding window. Pure, with the time passed in."""

from hopin.participation.vocabulary import LINE_WINDOW_SECONDS, LINES_PER_WINDOW


def admitted(window: tuple[float, ...], now: float) -> tuple[float, ...] | None:
    """The window with a line at `now` added, or nothing when the window already holds the most lines it may."""
    recent = tuple(written for written in window if now - written < LINE_WINDOW_SECONDS)
    if len(recent) >= LINES_PER_WINDOW:
        return None
    return (*recent, now)
