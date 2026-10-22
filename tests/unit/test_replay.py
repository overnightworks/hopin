import pytest

from hopin.config.constants import SignalType
from hopin.signaling.replay import ReplayLog

LOG_LIMIT = 3


def chat(text="hello"):
    return {"type": SignalType.CHAT.value, "message": text}


def description(signal_type, sender, sdp="sdp"):
    return {"type": signal_type.value, "from": sender, "sdp": sdp}


def candidate(sender):
    return {"type": SignalType.ICE_CANDIDATE.value, "from": sender, "candidate": "c"}


def seqs(frames):
    return [frame["seq"] for frame in frames]


class TestAppend:
    def test_numbers_each_frame_one_higher_than_the_last(self):
        log = ReplayLog()

        numbered = [log.append(chat()), log.append(chat()), log.append(chat())]

        assert seqs(numbered) == [1, 2, 3]
        assert numbered[0]["message"] == "hello"

    def test_leaves_the_frame_it_was_given_unnumbered(self):
        frame = chat()

        ReplayLog().append(frame)

        assert "seq" not in frame


class TestLength:
    def test_counts_the_frames_held_after_the_oldest_were_dropped_and_after_a_superseded_one_went(self):
        log = ReplayLog(limit=LOG_LIMIT)
        assert len(log) == 0

        for text in "abcde":
            log.append(chat(text))
        assert len(log) == LOG_LIMIT

        log.append(description(SignalType.OFFER, "alice"))
        log.append(description(SignalType.OFFER, "alice"))
        assert len(log) == LOG_LIMIT


class TestSince:
    def test_answers_the_kept_frames_after_the_given_seq_in_order(self):
        log = ReplayLog()
        for text in "abcd":
            log.append(chat(text))

        assert [frame["message"] for frame in log.since(2)] == ["c", "d"]

    def test_answers_nothing_when_the_reader_is_caught_up(self):
        log = ReplayLog()
        log.append(chat())

        assert log.since(1) == []


class TestSizeLimit:
    def test_drops_the_oldest_frame_and_raises_the_floor_to_its_seq(self):
        log = ReplayLog(limit=LOG_LIMIT)
        for _ in range(LOG_LIMIT + 2):
            log.append(chat())

        assert seqs(log.since(0)) == [3, 4, 5]
        assert not log.reaches_back_to(1)
        assert log.reaches_back_to(2)

    def test_reaches_back_to_anything_while_nothing_was_dropped(self):
        log = ReplayLog(limit=LOG_LIMIT)
        log.append(chat())

        assert log.reaches_back_to(0)


class TestSuperseding:
    @pytest.mark.parametrize("newer", [SignalType.OFFER, SignalType.ANSWER])
    def test_a_newer_description_drops_that_senders_earlier_descriptions_and_candidates_only(self, newer):
        log = ReplayLog()
        log.append(description(SignalType.OFFER, "alice"))
        log.append(candidate("alice"))
        log.append(description(SignalType.ANSWER, "alice"))
        log.append(description(SignalType.OFFER, "bob"))
        log.append(candidate("bob"))
        log.append(chat())

        log.append(description(newer, "alice", sdp="newest"))

        assert [(frame["type"], frame.get("from")) for frame in log.since(0)] == [
            (SignalType.OFFER.value, "bob"),
            (SignalType.ICE_CANDIDATE.value, "bob"),
            (SignalType.CHAT.value, None),
            (newer.value, "alice"),
        ]

    def test_dropping_superseded_frames_leaves_gaps_but_not_the_floor(self):
        log = ReplayLog()
        log.append(description(SignalType.OFFER, "alice"))
        log.append(description(SignalType.OFFER, "alice"))

        assert seqs(log.since(0)) == [2]
        assert log.reaches_back_to(0)

    def test_a_candidate_after_a_description_is_kept(self):
        log = ReplayLog()
        log.append(description(SignalType.OFFER, "alice"))
        log.append(candidate("alice"))

        assert seqs(log.since(0)) == [1, 2]
