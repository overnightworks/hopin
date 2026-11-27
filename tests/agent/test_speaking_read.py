"""The worker that reads a sentence's sound stops reading once the sentence is stopped."""

import threading

from hopin.agent.speaking import _read


def test_call_60_a_stop_is_seen_at_the_next_item_which_is_not_handed_over():
    """CALL-60."""
    stopped = threading.Event()
    delivered: list[object] = []
    closed = []

    def stream():
        try:
            yield "first"
            stopped.set()
            yield "second"
            yield "third"
        finally:
            closed.append(True)

    _read(stream(), delivered.append, stopped)

    assert (delivered, closed) == (["first"], [True])
