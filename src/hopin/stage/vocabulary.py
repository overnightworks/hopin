import re
from enum import StrEnum

FEATURE_NAME = "stage"
APP_NAME_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,31}")
# The key of a table in an app: opaque to HopIn, which keeps one bound for every app; the app answers an unknown one.
CONTEXT_KEY_PATTERN = r"[A-Za-z0-9_-]{1,64}"
CONTEXT_PLACEHOLDER = "{context}"
CALL_MODE_QUERY = "mode=call"


class StageSignal(StrEnum):
    STAGE_APPS = "stage_apps"
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
