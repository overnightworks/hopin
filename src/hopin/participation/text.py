"""What an agent's name and line may be: the cleaning every tool and frame of participation shares."""

import unicodedata
from typing import Final

from hopin.participation.vocabulary import AGENT_LINE_MAX_CHARACTERS, AGENT_NAME_MAX_CHARACTERS

CONTROL_CHARACTER: Final = "Cc"


def clean_name(name: str) -> str | None:
    """The name an agent shows under, or nothing when it is no name: 1 to 32 characters after trimming, none a
    control character."""
    trimmed = name.strip()
    if not 0 < len(trimmed) <= AGENT_NAME_MAX_CHARACTERS:
        return None
    if any(unicodedata.category(character) == CONTROL_CHARACTER for character in trimmed):
        return None
    return trimmed


def one_line(text: str) -> str:
    """The text with its line breaks and runs of spaces turned into one space, and no space at either end."""
    return " ".join(text.split())


def clean_line(text: str) -> str | None:
    """The line an agent writes, or nothing when it is no line: 1 to 600 characters after trimming, none a control
    character, a line break included."""
    trimmed = text.strip()
    if not 0 < len(trimmed) <= AGENT_LINE_MAX_CHARACTERS:
        return None
    if any(unicodedata.category(character) == CONTROL_CHARACTER for character in trimmed):
        return None
    return trimmed
