"""A quiz's rules: its rounds, its transcript and who may reveal or end. Pure; the table runs it."""

import json
from dataclasses import dataclass, field, replace
from enum import Enum, StrEnum, auto
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from hopin.signaling.frames import PeerId

QUIZ_ROUNDS: Final = 5
QUIZ_TOPICS: Final = (
    "Geschichte",
    "Geografie",
    "Naturwissenschaft",
    "Tiere",
    "Sport",
    "Musik",
    "Film und Fernsehen",
    "Literatur",
    "Essen und Trinken",
    "Technik",
    "Kunst",
    "Sprache",
    "Weltraum",
    "Erfindungen",
)
REVEAL_REQUEST: Final = (
    "Löse die Frage auf. Die nächste Zeile listet die Antworten der Mitspielenden als JSON, "
    'je Antwort mit "nr", "name" und "antwort"; eine leere Liste heißt, dass niemand geantwortet hat. '
    "Gib zuerst dein Urteil über jede Antwort mit dem Werkzeug record_verdicts ab, je Antwort ein Urteil mit ihrer "
    '"nr"; dann schreibst du die Auflösung.'
)
FINAL_SCORE_REQUEST: Final = (
    "Das war die letzte Frage. Die nächste Zeile nennt den gezählten Stand als JSON, je Person mit "
    '"name" und "richtig" (die Zahl der richtig beantworteten Fragen); eine leere Liste heißt, dass niemand '
    "geantwortet hat. Nenne den Endstand."
)


class Speaker(Enum):
    HOPIN = auto()
    GAME_MASTER = auto()


@dataclass(frozen=True)
class Turn:
    speaker: Speaker
    text: str


class QuizStep(Enum):
    ASKING = auto()
    ANSWERING = auto()
    REVEALING = auto()
    SCORING = auto()
    OVER = auto()


class Judgement(StrEnum):
    CORRECT = "correct"
    WRONG = "wrong"


@dataclass(frozen=True)
class Verdict:
    """The game master's judgement of one answer, named by its `nr` in the reveal request (counted from one)."""

    answer: int
    judgement: Judgement


@dataclass(frozen=True)
class PlayerAnswer:
    player: PeerId
    name: str = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True)
class Score:
    """How many questions of this quiz a player has answered correctly, by the verdicts."""

    player: PeerId
    name: str = field(repr=False)
    correct: int


def question_request(topic: str) -> str:
    return f"Stelle die nächste Quizfrage zum Thema {topic}."


def final_score_request(scores: tuple[Score, ...]) -> str:
    """The fixed request and, on its own line, the counted score as JSON, so the game master states and never counts."""
    counted = [{"name": score.name, "richtig": score.correct} for score in scores]
    return f"{FINAL_SCORE_REQUEST}\n{json.dumps(counted, ensure_ascii=False)}"


@dataclass(frozen=True)
class Quiz:
    """One quiz of a room: the transcript ends with the request of the run the step waits for.

    A round's players are the seats its question line was delivered to; each has at most one answer.
    Round n asks about the n-th of the quiz's topics, which differ from each other. The scores are counted from the
    verdicts of the reveals so far.
    """

    starter: PeerId
    topics: tuple[str, ...]
    step: QuizStep = QuizStep.ASKING
    round: int = 1
    transcript: tuple[Turn, ...] = ()
    players: frozenset[PeerId] = frozenset()
    answers: tuple[PlayerAnswer, ...] = ()
    scores: tuple[Score, ...] = ()

    @classmethod
    def opening(cls, starter: PeerId, topics: tuple[str, ...]) -> Quiz:
        return cls(starter, topics, transcript=(Turn(Speaker.HOPIN, question_request(topics[0])),))

    @property
    def is_asking(self) -> bool:
        return self.step is QuizStep.ASKING

    @property
    def is_revealing(self) -> bool:
        return self.step is QuizStep.REVEALING

    @property
    def awaits_game_master(self) -> bool:
        return self.step in {QuizStep.ASKING, QuizStep.REVEALING, QuizStep.SCORING}

    @property
    def is_over(self) -> bool:
        return self.step is QuizStep.OVER

    @property
    def is_opening(self) -> bool:
        """Whether the run the quiz waits for asks question one, before which nobody has played."""
        return self.is_asking and self.round == 1

    def may_end(self, peer_id: PeerId, *, hosts_call: bool) -> bool:
        return peer_id == self.starter or hosts_call

    def ended(self) -> Quiz:
        return replace(  # NOSONAR(S5886) replace returns a Quiz here
            self,
            step=QuizStep.OVER,
            transcript=(),
            players=frozenset(),
            answers=(),
        )

    def may_reveal(self, peer_id: PeerId) -> bool:
        return peer_id == self.starter and self.step is QuizStep.ANSWERING

    def may_answer(self, peer_id: PeerId) -> bool:
        return self.step is QuizStep.ANSWERING and peer_id in self.players

    def with_answer(self, player: PeerId, name: str, text: str) -> Quiz:
        """The quiz once the player's answer is in; it replaces the player's earlier one."""
        answers = (*(answer for answer in self.answers if answer.player != player), PlayerAnswer(player, name, text))
        return replace(self, answers=answers)  # NOSONAR(S5886) replace returns a Quiz here

    def counts_leave_of(self, peer_id: PeerId) -> bool:
        """Whether a player's leave changes what the seats are shown: only once the open question has an answer."""
        return self.step is QuizStep.ANSWERING and peer_id in self.players and bool(self.answers)

    def without_player(self, peer_id: PeerId) -> Quiz:
        """The quiz once the player has left for good; the player's given answer stays for the reveal request."""
        return replace(self, players=self.players - {peer_id})  # NOSONAR(S5886) replace returns a Quiz here

    def counted_players(self, present: frozenset[PeerId]) -> frozenset[PeerId]:
        """The present players whose answer the count holds."""
        return frozenset({answer.player for answer in self.answers} & present & self.players)

    def answered_count(self, present: frozenset[PeerId]) -> int:
        return len(self.counted_players(present))

    def everyone_answered(self, present: frozenset[PeerId]) -> bool:
        waited_for = self.players & present
        return bool(waited_for) and self.answered_count(present) == len(waited_for)

    def revealing(self) -> Quiz:
        transcript = (*self.transcript, Turn(Speaker.HOPIN, self._reveal_request()))
        return replace(  # NOSONAR(S5886) replace returns a Quiz here
            self,
            step=QuizStep.REVEALING,
            transcript=transcript,
        )

    def _reveal_request(self) -> str:
        """The fixed request and, on its own line, the given answers as JSON, so no name or answer can pose as more."""
        given = [
            {"nr": number, "name": answer.name, "antwort": answer.text}
            for number, answer in enumerate(self.answers, start=1)
        ]
        return f"{REVEAL_REQUEST}\n{json.dumps(given, ensure_ascii=False)}"

    def judges(self, verdicts: tuple[Verdict, ...]) -> bool:
        """Whether the verdicts name each given answer exactly once and nothing else."""
        return sorted(verdict.answer for verdict in verdicts) == list(range(1, len(self.answers) + 1))

    def heard(self, line: str, players: frozenset[PeerId], verdicts: tuple[Verdict, ...] = ()) -> Quiz:
        """The quiz once the game master's line answers the step's request, with the request that follows.

        The line of a question reaches the round's players. The verdicts of a reveal, which `judges` has accepted, add
        to the scores.
        """
        transcript = (*self.transcript, Turn(Speaker.GAME_MASTER, line))
        match self.step:
            case QuizStep.ASKING:
                return replace(  # NOSONAR(S5886) replace returns a Quiz here
                    self,
                    step=QuizStep.ANSWERING,
                    transcript=transcript,
                    players=players,
                    answers=(),
                )
            case QuizStep.REVEALING if self.round < QUIZ_ROUNDS:
                scored = self._scored(verdicts)
                transcript = (*transcript, Turn(Speaker.HOPIN, question_request(self.topics[self.round])))
                return replace(  # NOSONAR(S5886) replace returns a Quiz here
                    scored,
                    step=QuizStep.ASKING,
                    round=self.round + 1,
                    transcript=transcript,
                    players=frozenset(),
                    answers=(),
                )
            case QuizStep.REVEALING:
                scored = self._scored(verdicts)
                request = Turn(Speaker.HOPIN, final_score_request(scored.scores))
                return replace(  # NOSONAR(S5886) replace returns a Quiz here
                    scored, step=QuizStep.SCORING, transcript=(*transcript, request), players=frozenset(), answers=()
                )
            case _:
                return replace(  # NOSONAR(S5886) replace returns a Quiz here
                    self,
                    step=QuizStep.OVER,
                    transcript=transcript,
                    players=frozenset(),
                    answers=(),
                )

    def _scored(self, verdicts: tuple[Verdict, ...]) -> Quiz:
        """The quiz with every given answer's player on the scores, a correct verdict adding one."""
        points = {score.player: score for score in self.scores}
        for number, answer in enumerate(self.answers, start=1):
            current = points.get(answer.player, Score(answer.player, answer.name, 0))
            correct = any(verdict.answer == number and verdict.judgement is Judgement.CORRECT for verdict in verdicts)
            points[answer.player] = replace(current, correct=current.correct + int(correct))
        return replace(self, scores=tuple(points.values()))  # NOSONAR(S5886) replace returns a Quiz here
