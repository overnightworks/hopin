import pytest

from hopin.config.rate import admitted
from hopin.participation.look_rate import LOOK_RATE
from hopin.participation.vocabulary import LOOK_WINDOW_SECONDS, LOOKS_PER_WINDOW


def fill(now):
    window = ()
    for _ in range(LOOKS_PER_WINDOW):
        window = admitted(window, now, LOOK_RATE)
    return window


class TestTheWindowOfLooks:
    def test_a_look_is_admitted_while_the_window_holds_fewer_than_the_limit(self):
        assert admitted(fill(0.0)[1:], 0.0, LOOK_RATE) is not None

    def test_a_look_over_the_limit_within_the_window_is_not_admitted(self):
        assert admitted(fill(0.0), LOOK_WINDOW_SECONDS - 1, LOOK_RATE) is None

    def test_a_look_when_the_window_has_passed_is_admitted(self):
        assert admitted(fill(0.0), LOOK_WINDOW_SECONDS, LOOK_RATE) is not None

    def test_looks_that_fell_out_of_the_window_do_not_stay_in_it(self):
        assert admitted(fill(0.0), LOOK_WINDOW_SECONDS, LOOK_RATE) == (LOOK_WINDOW_SECONDS,)

    @pytest.mark.parametrize("seconds_apart", [0.0, 1.0, 59.0])
    def test_the_oldest_look_leaves_the_window_exactly_when_the_window_has_passed(self, seconds_apart):
        window = admitted((), 0.0, LOOK_RATE)
        for _ in range(LOOKS_PER_WINDOW - 1):
            window = admitted(window, seconds_apart, LOOK_RATE)

        assert admitted(window, LOOK_WINDOW_SECONDS - 0.5, LOOK_RATE) is None
        assert admitted(window, LOOK_WINDOW_SECONDS, LOOK_RATE) is not None
