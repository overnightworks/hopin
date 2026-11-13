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


def talked_over(window):
    record = window.read().talked_over
    return None if record is None else (record.id, record.playing, [(p.name, p.text) for p in record.speech])


class TestBeingTalkedOver:
    def test_call_54_a_person_starting_to_speak_while_a_sentence_plays_opens_a_record_with_no_words_yet(self):
        window = ChatWindow("generation")
        window.set_playing(playing=True)

        changed = window.talk_over("seat-a", "Ada")

        assert (changed, talked_over(window)) == (True, ("1", True, [("Ada", "")]))

    def test_call_54_a_person_starting_to_speak_while_no_sentence_plays_opens_no_record(self):
        window = ChatWindow("generation")

        changed = window.talk_over("seat-a", "Ada")

        assert (changed, talked_over(window)) == (False, None)

    def test_call_54_a_second_person_adds_a_partial_under_the_same_id_in_the_order_they_started(self):
        window = ChatWindow("generation")
        window.set_playing(playing=True)
        window.talk_over("seat-a", "Ada")

        window.talk_over("seat-b", "Bo")
        window.hear_partial("seat-b", "Moment")

        assert talked_over(window) == ("1", True, [("Ada", ""), ("Bo", "Moment")])

    def test_call_54_a_person_who_starts_speaking_again_in_the_same_turn_changes_nothing(self):
        window = ChatWindow("generation")
        window.set_playing(playing=True)
        window.talk_over("seat-a", "Ada")
        window.hear_partial("seat-a", "Hallo")

        changed = window.talk_over("seat-a", "Ada")

        assert (changed, talked_over(window)) == (False, ("1", True, [("Ada", "Hallo")]))

    def test_call_54_the_words_heard_so_far_replace_the_partial_of_that_person_only(self):
        window = ChatWindow("generation")
        window.set_playing(playing=True)
        window.talk_over("seat-a", "Ada")
        window.talk_over("seat-b", "Bo")

        window.hear_partial("seat-a", "Hal")
        window.hear_partial("seat-a", "Hallo")

        assert talked_over(window) == ("1", True, [("Ada", "Hallo"), ("Bo", "")])

    def test_call_54_the_record_goes_with_the_last_turn_and_the_next_record_has_a_new_id(self):
        window = ChatWindow("generation")
        window.set_playing(playing=True)
        window.talk_over("seat-a", "Ada")
        window.talk_over("seat-b", "Bo")

        window.turn_ended("seat-a")
        while_bo_speaks = talked_over(window)
        window.turn_ended("seat-b")
        gone = talked_over(window)
        window.talk_over("seat-a", "Ada")

        assert (while_bo_speaks, gone, talked_over(window)) == (
            ("1", True, [("Bo", "")]),
            None,
            ("2", True, [("Ada", "")]),
        )

    def test_call_54_the_record_says_whether_a_sentence_plays_and_keeps_its_id_while_it_stands(self):
        window = ChatWindow("generation")
        window.set_playing(playing=True)
        window.talk_over("seat-a", "Ada")

        window.set_playing(playing=False)
        silent = talked_over(window)
        window.set_playing(playing=True)

        assert (silent, talked_over(window)) == (("1", False, [("Ada", "")]), ("1", True, [("Ada", "")]))

    def test_call_54_a_person_starting_to_speak_after_the_sentence_ended_joins_the_record_that_stands(self):
        window = ChatWindow("generation")
        window.set_playing(playing=True)
        window.talk_over("seat-a", "Ada")
        window.set_playing(playing=False)

        window.talk_over("seat-b", "Bo")

        assert talked_over(window) == ("1", False, [("Ada", ""), ("Bo", "")])

    def test_call_54_words_that_do_not_encode_as_utf8_and_words_heard_again_change_nothing(self):
        window = ChatWindow("generation")
        window.set_playing(playing=True)
        window.talk_over("seat-a", "Ada")
        window.hear_partial("seat-a", "Hallo")

        changes = (window.hear_partial("seat-a", f"broken {SURROGATE}"), window.hear_partial("seat-a", "Hallo"))

        assert (changes, talked_over(window)) == ((False, False), ("1", True, [("Ada", "Hallo")]))

    def test_call_54_words_and_an_end_of_a_person_who_started_no_turn_change_nothing(self):
        window = ChatWindow("generation")
        window.set_playing(playing=True)
        window.talk_over("seat-a", "Ada")

        changes = (window.hear_partial("seat-b", "Hi"), window.turn_ended("seat-b"), window.set_playing(playing=True))

        assert (changes, talked_over(window)) == ((False, False, False), ("1", True, [("Ada", "")]))

    def test_call_60_a_cut_line_is_marked_cut(self):
        window = ChatWindow("generation")

        window.append(LineKind.AGENT, "Mozart", "Guten", own=True, spoken=True, cut=True)

        assert window.read().lines[0].cut is True
