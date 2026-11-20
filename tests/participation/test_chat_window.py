import pytest

from hopin.participation.chat_window import ChatWindow, LineKind

SURROGATE = "\ud800"


def read(window, *, interrupted=None, playing=False):
    return window.read(interrupted=interrupted, playing=playing)


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

        assert (added, [line.seq for line in read(window).lines]) == (False, [1])


def talked_over(window, *, playing=False):
    record = read(window, playing=playing).talked_over
    return None if record is None else (record.id, record.playing, [(p.name, p.text) for p in record.speech])


def talked_over_while_speaking(window, seat="seat-a", name="Ada"):
    return window.talk_over(seat, name, opens=True)


class TestBeingTalkedOver:
    def test_call_54_a_person_starting_to_speak_while_the_agent_speaks_opens_a_record(self):
        window = ChatWindow("generation")

        changed = talked_over_while_speaking(window)

        assert (changed, talked_over(window)) == (True, ("1", False, [("Ada", "")]))

    def test_call_54_a_person_starting_to_speak_while_the_agent_is_silent_opens_no_record(self):
        window = ChatWindow("generation")

        changed = window.talk_over("seat-a", "Ada", opens=False)

        assert (changed, talked_over(window)) == (False, None)

    def test_call_54_a_second_person_adds_a_partial_under_the_same_id_in_the_order_they_started(self):
        window = ChatWindow("generation")
        talked_over_while_speaking(window)

        window.talk_over("seat-b", "Bo", opens=True)
        window.hear_partial("seat-b", "Moment")

        assert talked_over(window) == ("1", False, [("Ada", ""), ("Bo", "Moment")])

    def test_call_54_a_person_who_starts_speaking_again_in_the_same_turn_changes_nothing(self):
        window = ChatWindow("generation")
        talked_over_while_speaking(window)
        window.hear_partial("seat-a", "Hallo")

        changed = talked_over_while_speaking(window)

        assert (changed, talked_over(window)) == (False, ("1", False, [("Ada", "Hallo")]))

    def test_call_54_the_words_heard_so_far_replace_the_partial_of_that_person_only(self):
        window = ChatWindow("generation")
        talked_over_while_speaking(window)
        window.talk_over("seat-b", "Bo", opens=True)

        window.hear_partial("seat-a", "Hal")
        window.hear_partial("seat-a", "Hallo")

        assert talked_over(window) == ("1", False, [("Ada", "Hallo"), ("Bo", "")])

    def test_call_54_the_record_goes_with_the_last_turn_and_the_next_record_has_a_new_id(self):
        window = ChatWindow("generation")
        talked_over_while_speaking(window)
        window.talk_over("seat-b", "Bo", opens=True)

        window.turn_ended("seat-a")
        while_bo_speaks = talked_over(window)
        window.turn_ended("seat-b")
        gone = talked_over(window)
        window.talk_over("seat-a", "Ada", opens=True)

        assert (while_bo_speaks, gone, talked_over(window)) == (
            ("1", False, [("Bo", "")]),
            None,
            ("2", False, [("Ada", "")]),
        )

    def test_call_54_the_record_says_the_playing_its_reader_gives_and_keeps_its_id_while_it_stands(self):
        window = ChatWindow("generation")
        talked_over_while_speaking(window)

        assert (talked_over(window, playing=True), talked_over(window, playing=False)) == (
            ("1", True, [("Ada", "")]),
            ("1", False, [("Ada", "")]),
        )

    def test_call_54_a_person_starting_to_speak_after_the_sentence_ended_joins_the_record_that_stands(self):
        window = ChatWindow("generation")
        talked_over_while_speaking(window)

        changed = window.talk_over("seat-b", "Bo", opens=False)

        assert (changed, talked_over(window)) == (True, ("1", False, [("Ada", ""), ("Bo", "")]))

    def test_call_54_words_that_do_not_encode_as_utf8_and_words_heard_again_change_nothing(self):
        window = ChatWindow("generation")
        talked_over_while_speaking(window)
        window.hear_partial("seat-a", "Hallo")

        changes = (window.hear_partial("seat-a", f"broken {SURROGATE}"), window.hear_partial("seat-a", "Hallo"))

        assert (changes, talked_over(window)) == ((False, False), ("1", False, [("Ada", "Hallo")]))

    def test_call_54_words_and_an_end_of_a_person_who_started_no_turn_change_nothing(self):
        window = ChatWindow("generation")
        talked_over_while_speaking(window)

        changes = (window.hear_partial("seat-b", "Hi"), window.turn_ended("seat-b"))

        assert (changes, talked_over(window)) == ((False, False), ("1", False, [("Ada", "")]))


class TestTheHold:
    def test_call_69_a_hold_id_is_the_windows_own_and_never_repeats_within_its_generation(self):
        window = ChatWindow("generation")

        assert [window.new_hold_id() for _ in range(3)] == ["1", "2", "3"]

    def test_call_69_a_read_says_the_hold_its_reader_gives_whether_or_not_a_record_stands(self):
        window = ChatWindow("generation")

        window_read = read(window, interrupted="1")

        assert (window_read.interrupted, window_read.talked_over) == ("1", None)

    def test_call_69_a_cut_line_is_marked_cut(self):
        window = ChatWindow("generation")

        window.append(LineKind.AGENT, "Mozart", "Guten", own=True, spoken=True, cut=True)

        assert read(window).lines[0].cut is True

    def test_call_60_a_line_that_continues_into_the_speakers_next_sentence_is_marked_continues(self):
        window = ChatWindow("generation")

        window.append(LineKind.AGENT, "Mozart", "Guten", own=True, spoken=True, continues=True)
        window.append(LineKind.AGENT, "Mozart", "Tag", own=True, spoken=True)

        assert [line.continues for line in read(window).lines] == [True, False]
