import re
from enum import StrEnum

FEATURE_NAME = "stage"
TABLE_PATH_PREFIX = "/t/"
TABLE_PATH_PATTERN = re.compile(re.escape(TABLE_PATH_PREFIX) + "[0-9a-f]{32}")
CALL_MODE_QUERY = "mode=call"


class StageSignal(StrEnum):
    STAGE_ATTACH = "stage_attach"
    STAGE_DETACH = "stage_detach"
    STAGE_TABLE = "stage_table"
    STAGE_REFUSED = "stage_refused"


class StageRefusal(StrEnum):
    """Why an attach or a detach is refused; the seat that asked is told it."""

    BAD_LINK = "bad_link"
    BUSY = "busy"
    NOT_OPENER_OR_HOST = "not_opener_or_host"


def _members(vocabulary: type[StrEnum]) -> dict[str, str]:
    return {name: member.value for name, member in vocabulary.__members__.items()}


SIGNAL_TYPES = _members(StageSignal)
# The enums the generator renders into the feature's own browser wire, by their exported name.
BROWSER_ENUMS = {"STAGE_SIGNAL": StageSignal, "STAGE_REFUSAL": StageRefusal}
