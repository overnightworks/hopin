import re
from enum import StrEnum

FEATURE_NAME = "skat"
TABLE_PATH_PATTERN = re.compile(r"/t/[0-9a-f]{32}")
CALL_MODE_QUERY = "mode=call"


class SkatSignal(StrEnum):
    SKAT_ATTACH = "skat_attach"
    SKAT_DETACH = "skat_detach"
    SKAT_TABLE = "skat_table"
    SKAT_REFUSED = "skat_refused"


class SkatRefusal(StrEnum):
    """Why an attach or a detach is refused; the seat that asked is told it."""

    BAD_LINK = "bad_link"
    BUSY = "busy"
    NOT_OPENER_OR_HOST = "not_opener_or_host"


def _members(vocabulary: type[StrEnum]) -> dict[str, str]:
    return {name: member.value for name, member in vocabulary.__members__.items()}


SIGNAL_TYPES = _members(SkatSignal)
# The enums the generator renders into the feature's own browser wire, by their exported name.
BROWSER_ENUMS = {"SKAT_SIGNAL": SkatSignal, "SKAT_REFUSAL": SkatRefusal}
