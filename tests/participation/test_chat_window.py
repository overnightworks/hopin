import pytest

from hopin.participation.chat_window import ChatWindow, LineKind

SURROGATE = "\ud800"


class TestAdmission:
    @pytest.mark.parametrize(
        ("name", "text"),
        [
            pytest.param("Ada", "", id="empty_text"),
            pytest.param("", "hello", id="empty_name"),
            pytest.param("Ada", f"broken {SURROGATE}", id="lone_surrogate_in_the_text"),
            pytest.param(f"Ada {SURROGATE}", "hello", id="lone_surrogate_in_the_name"),
        ],
    )
    def test_call_54_a_line_that_cannot_be_read_is_refused_and_takes_no_seq(self, name, text):
        """CALL-54: An empty line, or one that does not encode as UTF-8, is not held and takes no seq."""
        window = ChatWindow("generation")

        added = window.append(LineKind.PERSON, name, text, own=False)
        window.append(LineKind.PERSON, "Ada", "fine", own=False)

        assert (added, [line.seq for line in window.read().lines]) == (False, [1])
