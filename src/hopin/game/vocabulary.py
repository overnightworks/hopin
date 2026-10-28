from enum import StrEnum

from hopin.config.constants import STATIC_PATH

GAME_LINE_MAX_CHARACTERS = 600
GAME_ANSWER_MAX_CHARACTERS = 200
GAME_RETRY_AFTER_MAX_SECONDS = 600
GAME_RETRY_AFTER_FALLBACK_SECONDS = 30
# A run rejection is one small JSON object; a longer answer is no rejection and is not read further.
GAME_REJECTION_MAX_BYTES = 16 * 1024
# Four of the agent service's fifteen-second heartbeats.
GAME_READ_TIMEOUT_SECONDS = 60
GAME_RUN_DEADLINE_SECONDS = 90


class GameSignal(StrEnum):
    GAME_START = "game_start"
    GAME_REVEAL = "game_reveal"
    GAME_ANSWER = "game_answer"
    GAME_ANSWER_COUNT = "game_answer_count"
    GAME_PHASE = "game_phase"
    GAME_LINE = "game_line"
    GAME_REFUSED = "game_refused"


class GamePhase(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    ANSWERING = "answering"


class GameRefusal(StrEnum):
    NO_ACCESS = "no_access"
    SIGN_IN_AGAIN = "sign_in_again"
    BUSY = "busy"
    ALREADY_ANSWERING = "already_answering"
    QUIZ_RUNNING = "quiz_running"
    NOT_STARTER = "not_starter"
    ANSWER_CLOSED = "answer_closed"
    ANSWER_TOO_LONG = "answer_too_long"
    UNAVAILABLE = "unavailable"


def _members(vocabulary: type[StrEnum]) -> dict[str, str]:
    return {name: member.value for name, member in vocabulary.__members__.items()}


# What the call core adds to the browser's wire module while the game is wired in.
SIGNAL_TYPES = _members(GameSignal)
WIRE_EXPORTS = {"GAME_PHASE": _members(GamePhase), "GAME_REFUSAL": _members(GameRefusal)}
BROWSER_MODULE = f"{STATIC_PATH}/game/game.js"
