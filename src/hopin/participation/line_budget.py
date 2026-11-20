"""How fast one seat may write lines for the agents of its call: a budget that holds the limits' burst and refills at
their rate, which the browser's sender paces itself to. Pure, with the time passed in."""

from dataclasses import dataclass

from hopin.config.constants import LINE_LIMITS


@dataclass(frozen=True)
class LineBudget:
    """The lines a seat may still write at once, counted when `counted_at` on the deadlines' clock was."""

    lines: float
    counted_at: float


def full_budget(now: float) -> LineBudget:
    return LineBudget(float(LINE_LIMITS.burst_lines), now)


def spent_on_a_line(budget: LineBudget, now: float) -> LineBudget | None:
    """The budget once a line at `now` is written, or nothing when it holds no whole line."""
    refilled = min(
        float(LINE_LIMITS.burst_lines), budget.lines + max(0.0, now - budget.counted_at) * LINE_LIMITS.lines_per_second
    )
    if refilled < 1:
        return None
    return LineBudget(refilled - 1, now)
