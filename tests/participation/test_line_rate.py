import pytest

from hopin.participation.line_rate import LINE_RATE, admitted
from hopin.participation.vocabulary import LINE_WINDOW_SECONDS, LINES_PER_WINDOW


def fill(now):
    window = ()
    for _ in range(LINES_PER_WINDOW):
        window = admitted(window, now, LINE_RATE)
    return window


class TestTheWindowOfLines:
    def test_a_line_is_admitted_while_the_window_holds_fewer_than_the_limit(self):
        assert admitted(fill(0.0)[1:], 0.0, LINE_RATE) is not None

    def test_a_line_over_the_limit_within_the_window_is_not_admitted(self):
        assert admitted(fill(0.0), LINE_WINDOW_SECONDS - 1, LINE_RATE) is None

    def test_a_line_when_the_window_has_passed_is_admitted(self):
        assert admitted(fill(0.0), LINE_WINDOW_SECONDS, LINE_RATE) is not None

    def test_lines_that_fell_out_of_the_window_do_not_stay_in_it(self):
        assert admitted(fill(0.0), LINE_WINDOW_SECONDS, LINE_RATE) == (LINE_WINDOW_SECONDS,)

    @pytest.mark.parametrize("seconds_apart", [0.0, 1.0, 59.0])
    def test_the_oldest_line_leaves_the_window_exactly_when_the_window_has_passed(self, seconds_apart):
        window = admitted((), 0.0, LINE_RATE)
        for _ in range(LINES_PER_WINDOW - 1):
            window = admitted(window, seconds_apart, LINE_RATE)

        assert admitted(window, LINE_WINDOW_SECONDS - 0.5, LINE_RATE) is None
        assert admitted(window, LINE_WINDOW_SECONDS, LINE_RATE) is not None
